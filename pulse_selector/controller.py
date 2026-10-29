"""Top-level import and tab composition; state/capture actions live in their controllers."""

from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from .core import prepare_signals, read_log
from .models import ApplicationModel
from .view import ColumnMappingDialog, PulseSelectorWindow


class PulseController:
    def __init__(self, model=None):
        self.model = model if model is not None else ApplicationModel()
        self.view = PulseSelectorWindow(self)
        from .pulse_capture_controller import PulseCaptureController
        from .calibration_controller import CalibrationController

        self.pulses = PulseCaptureController(self, self.view)
        self.calibration = CalibrationController(self, self.view)
        self.view.tabs.addTab(self.pulses.view, "Pulse picker")
        self.view.tabs.addTab(self.calibration.roi_view, "Check-plug ROI")
        self.view.tabs.addTab(self.calibration.fit_view, "Volume calculation")
        self.set_pressure_unit(self.model.pressure_display_unit)

    def open_log(self):
        """Read a file, ask how its columns map to sensors, then load both workflows."""
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
            date_column, time_column, channels = dialog.mapping()
            self.load_frame(frame, path, date_column, time_column, channels)
        except Exception as exc:
            QMessageBox.warning(self.view, "Could not prepare log", str(exc))

    def load_frame(self, frame, path, date_column, time_column, channels):
        """Prepare a recording completely before replacing the current model."""
        clean, description, report = prepare_signals(
            frame, date_column, time_column, channels
        )
        self.model.data = clean
        self.model.source_path = Path(path)
        self.model.date_column = date_column
        self.model.time_column = time_column
        self.model.channels = list(channels)
        self.model.time_description = description
        self.model.import_report = report

        # Both workflows receive the same imported signals, but keep their own
        # collections of captured pulses or calibration runs.
        self.pulses.log_loaded(self.model)
        self.calibration.log_loaded(self.model)
        issues = []
        for key, label in [
            ("repaired_date_rows", "isolated date rows repaired"),
            ("invalid_time_rows", "invalid-time rows removed"),
            ("nonincreasing_rows", "non-increasing rows removed"),
        ]:
            if report[key]:
                issues.append(f"{report[key]} {label}")
        missing = sum(report["missing_samples"].values())
        if missing:
            issues.append(f"{missing} missing channel readings retained as gaps")
        self.view.statusBar().showMessage(
            f"Loaded {self.model.source_path.name}: {len(clean):,} rows. "
            "Captured pulses and calibration runs retained."
            + (" " + "; ".join(issues) if issues else "")
        )

    def export_active_tab(self):
        """Route the shared toolbar button to the appropriate collection."""
        if self.view.tabs.currentIndex() == 0:
            self.pulses.export()
        else:
            self.calibration.export()

    def set_pressure_unit(self,unit):
        """Change presentation without changing recordings, captures or fit inputs."""
        self.model.pressure_display_unit=unit
        self.pulses.view.barometric.set_pressure_unit(unit)
        self.pulses.view.redraw()
        self.pulses.preview()
        roi=self.calibration.roi_view
        for spin in (roi.barometric,roi.receiving_pressure,roi.p_limit):
            spin.set_pressure_unit(unit)
        roi.redraw()
        self.calibration.refresh_preview()
        self.calibration.refresh_runs()
        self.calibration.fit_view.set_pressure_unit(unit)
