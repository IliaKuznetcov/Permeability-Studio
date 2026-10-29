"""Coordinate user actions, core calculations, model updates and rendering."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from .core import calculate_plateau, create_selection, prepare_signals, read_log
from .export import write_workbook
from .models import ApplicationModel
from .legacy_view import ColumnMappingDialog, PulseSelectorWindow


class LegacyPulseController:
    def __init__(self, model: ApplicationModel | None = None):
        self.model = model if model is not None else ApplicationModel()
        self.view = PulseSelectorWindow(self)
        from .calibration_controller import CalibrationController

        self.calibration = CalibrationController(self, self.view)
        self.view.tabs.addTab(self.calibration.roi_view, "Check-plug ROI")
        self.view.tabs.addTab(self.calibration.fit_view, "Volume calculation")

    def open_log(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self.view,
            "Open measurement log",
            "",
            "Data files (*.xlsx *.xls *.csv);;Excel files (*.xlsx *.xls);;CSV files (*.csv)",
        )
        if not filename:
            return
        path = Path(filename)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.view.statusBar().showMessage(f"Reading {path.name} ...")
        QApplication.processEvents()
        try:
            frame = read_log(path)
        except Exception as exc:
            QMessageBox.critical(self.view, "Could not read file", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        dialog = ColumnMappingDialog(frame, self.view)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.load_frame(frame, path, *dialog.mapping())
        except Exception as exc:
            QMessageBox.warning(self.view, "Could not prepare log", str(exc))

    def load_frame(self, frame, path, date_column, time_column, channels) -> None:
        """Validate a recording fully before discarding the current selections."""
        clean, description, report = prepare_signals(
            frame, date_column, time_column, channels
        )
        self.clear_selections()
        model = self.model
        model.data, model.source_path = clean, Path(path)
        model.date_column, model.time_column, model.channels = (
            date_column,
            time_column,
            list(channels),
        )
        model.time_description, model.import_report = description, report
        self.view._plot_channels()
        self.view._refresh_table()
        self.view.capture_button.setEnabled(True)
        self.calibration.log_loaded(model)
        issues = []
        for key, text in [
            ("repaired_date_rows", "isolated date rows repaired"),
            ("invalid_time_rows", "invalid-time rows removed"),
            ("nonincreasing_rows", "non-increasing rows removed"),
        ]:
            if report[key]:
                issues.append(f"{report[key]} {text}")
        missing = sum(report["missing_samples"].values())
        if missing:
            issues.append(f"{missing} missing channel readings retained as gaps")
        message = f"Loaded {model.source_path.name}: {len(clean):,} rows, {len(channels)} channels. Click near a pulse."
        self.view.statusBar().showMessage(
            message + (" " + "; ".join(issues) + "." if issues else "")
        )

    def preview_plateau(self, bounds):
        """Calculate a preview without committing it to any captured pulse."""
        if self.model.data is None:
            raise ValueError("Open a recording first.")
        return calculate_plateau(self.model.data, self.model.channels, bounds)

    def capture_selection(self) -> None:
        view, model = self.view, self.model
        if (
            model.data is None
            or not view.region.isVisible()
            or not view.plateau_region.isVisible()
        ):
            QMessageBox.information(
                view,
                "No selection",
                "Click on the plot to position the pulse and plateau windows.",
            )
            return
        try:
            selection = create_selection(
                model.data,
                model.channels,
                len(model.selections) + 1,
                view.region.getRegion(),
                view.plateau_region.getRegion(),
                view.pre_context_spin.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(view, "Cannot capture selection", str(exc))
            return
        model.selections.append(selection)
        view._add_overlay(selection)
        view._refresh_table()
        view.table.selectRow(len(model.selections) - 1)
        view._update_buttons()
        start, end = selection.start_time_s, selection.end_time_s
        width = end - start
        time_max = float(model.data["Time"].iloc[-1])
        next_left = min(end + 0.05 * width, time_max)
        next_right = min(next_left + width, time_max)
        if next_right > next_left:
            view.region.setRegion((next_left, next_right))
            view.reset_plateau_to_tail()
        view.statusBar().showMessage(
            f"Captured pulse {selection.number} and plateau {selection.plateau.start_time_s:g}–"
            f"{selection.plateau.end_time_s:g} s. Check both windows for the next pulse."
        )

    def update_selected_plateau(self) -> None:
        view, model = self.view, self.model
        row = view.table.currentRow()
        if model.data is None or not 0 <= row < len(model.selections):
            return
        try:
            plateau = calculate_plateau(
                model.data, model.channels, view.plateau_region.getRegion()
            )
        except ValueError as exc:
            QMessageBox.warning(view, "Cannot update plateau", str(exc))
            return
        selection = model.selections[row]
        selection.plateau = plateau
        view.selection_overlays[row][1].setRegion(
            (plateau.start_time_s, plateau.end_time_s)
        )
        view._refresh_table()
        view.table.selectRow(row)
        view.statusBar().showMessage(f"Updated plateau for pulse {selection.number}.")

    def undo_last(self) -> None:
        if not self.model.selections:
            return
        self.model.selections.pop()
        for item in self.view.selection_overlays.pop():
            self.view.plot.removeItem(item)
        self.view._refresh_table()
        self.view._update_buttons()

    def clear_selections(self) -> None:
        for items in self.view.selection_overlays:
            for item in items:
                self.view.plot.removeItem(item)
        self.view.selection_overlays.clear()
        self.model.selections.clear()
        self.view._refresh_table()
        self.view._update_buttons()

    def export_selections(self) -> None:
        model, view = self.model, self.view
        if model.data is None or not model.selections:
            QMessageBox.information(
                view, "Nothing to export", "Capture at least one pulse first."
            )
            return
        suggested = (
            model.source_path.with_name(
                model.source_path.stem + "_selected_pulses.xlsx"
            )
            if model.source_path
            else Path("selected_pulses.xlsx")
        )
        filename, _ = QFileDialog.getSaveFileName(
            view, "Export captured pulses", str(suggested), "Excel workbook (*.xlsx)"
        )
        if not filename:
            return
        output = Path(filename)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        try:
            write_workbook(model, output)
        except Exception as exc:
            QMessageBox.critical(view, "Export failed", str(exc))
            return
        view.statusBar().showMessage(
            f"Exported {len(model.selections)} pulses to {output}"
        )
        QMessageBox.information(
            view,
            "Export complete",
            f"Saved {len(model.selections)} captured pulses to:\n{output}",
        )

    def export_active_tab(self):
        if self.view.tabs.currentIndex() == 0:
            self.export_selections()
        else:
            self.calibration.export()
