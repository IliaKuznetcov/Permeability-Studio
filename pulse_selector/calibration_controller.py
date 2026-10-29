"""Coordinate calibration views with extraction, storage and the independent solver."""

from __future__ import annotations
from pathlib import Path
import re
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .calibration_models import CalibrationProject, ROI_ROLES
from .state_extraction import snapshot_log, extract_roi, build_run, run_warnings
from .calibration import fit_project_runs, inputs_for_run
from .calibration_io import save_session, load_session, write_calibration_workbook
from .calibration_view import CalibrationROIWidget, CalibrationFitWidget, item


class CalibrationController:
    """Own both calibration tabs and their shared collection of check-plug runs."""

    def __init__(self, main, parent):
        self.main, self.parent = main, parent
        self.project = CalibrationProject()
        self.active_snapshot = None
        self.edit_uid = None
        self.session_path = None
        self.dirty = False
        self.removed = []
        self.roi_view = CalibrationROIWidget(self)
        self.fit_view = CalibrationFitWidget(self)

    def log_loaded(self, model):
        """Display a recording and suggest editable defaults from its file name."""
        snapshot = snapshot_log(model)
        self.project.snapshots[snapshot.dataset_id] = snapshot
        self.active_snapshot = snapshot
        self.edit_uid = None
        self.roi_view.set_snapshot(snapshot)
        filename = Path(snapshot.source_path).stem
        side = "Downstream" if "downstream" in filename.lower() else "Upstream"
        channel = None
        for candidate in snapshot.channels:
            if candidate.quantity == "Pressure":
                channel = candidate
                break
        if channel and channel.location in ("Upstream", "Downstream"):
            side = channel.location
        self.roi_view.side.setCurrentText(side)
        self.side_changed()
        match = re.search(r"bore[_ -]?(\d+)", filename, re.I)
        solid = "solid" in filename.lower()
        self.roi_view.configuration.setCurrentText(
            "Solid isolating" if solid else "Through-bore"
        )
        if solid:
            plug_id = "Solid"
        elif match:
            plug_id = f"Hollow-{match[1]}"
        else:
            plug_id = ""
        self.roi_view.plug_id.setText(plug_id)
        self.roi_view.plug_volume.setValue(0)
        self.new_run()

    def side_changed(self, *_args):
        if not self.active_snapshot or self.roi_view._syncing:
            return
        side = self.roi_view.side.currentText()
        channel = None
        for candidate in self.active_snapshot.channels:
            if candidate.quantity == "Pressure" and candidate.location == side:
                channel = candidate
                break
        if channel:
            combo = self.roi_view.channel_combos["P"]
            combo.setCurrentIndex(combo.findData(channel.key))

    def calculate_geometry(self):
        view = self.roi_view
        if view.configuration.currentText() == "Solid isolating":
            view.plug_volume.setValue(0)
            return
        diameter, length = view.bore_id.value(), view.bore_length.value()
        if diameter <= 0 or length <= 0:
            QMessageBox.warning(
                view,
                "Plug geometry",
                "Enter a positive measured bore ID and gas-accessible length.",
            )
            return
        view.plug_volume.setValue(float(np.pi * diameter**2 * length / 4000))

    def refresh_preview(self):
        """Calculate the draft ROI statistics without adding a calibration run."""
        if not self.active_snapshot:
            return
        values, errors = {}, {}
        view = self.roi_view
        for role, key in view.channel_roles().items():
            if not key:
                continue
            try:
                values[role] = extract_roi(
                    self.active_snapshot, key, view.bounds()[role], role
                )
            except ValueError as exc:
                errors[role] = str(exc)
        view.show_statistics(values, errors)

    def _draft(self, uid=None):
        """Read the form, validate its regions and build a candidate run."""
        view = self.roi_view
        if not self.active_snapshot:
            raise ValueError("Open a recording first.")
        run = build_run(
            self.active_snapshot,
            bounds=view.bounds(),
            channel_roles=view.channel_roles(),
            label=view.label_edit.text(),
            side=view.side.currentText(),
            plug_id=view.plug_id.text(),
            configuration=view.configuration.currentText(),
            plug_volume_cm3=view.plug_volume.value(),
            opening_time_s=view.opening.value(),
            pressure_basis=view.pressure_basis.currentText(),
            barometric_kpa=view.barometric.canonical_value() or None,
            receiving_mode=view.receiving_mode.currentText(),
            receiving_pressure_kpa=view.receiving_pressure.canonical_value(),
            receiving_temperature_c=(
                view.receiving_temperature.value()
                if view.receiving_temperature.value() > -273.15
                else None
            ),
            receiving_temperature_source=view.receiving_source.text(),
            final_temperature_confirmed=view.final_confirmed.isChecked(),
            zi_override=view.zi.value() or None,
            zf_override=view.zf.value() or None,
            zb=view.zb.value(),
            included=view.include.isChecked(),
            exclusion_reason=view.exclusion.text(),
            notes=view.notes.text(),
            pressure_drift_limit_kpa_min=view.p_limit.canonical_value() or None,
            temperature_drift_limit_c_min=view.t_limit.value() or None,
            uid=uid,
        )
        if any(r.label == run.label and r.uid != run.uid for r in self.project.runs):
            raise ValueError(
                "This run ID is already captured. Use Save edits to run, or enter a new run ID."
            )
        return run

    def capture(self):
        """Add a valid run to memory; saving the session remains a separate action."""
        try:
            run = self._draft()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self.roi_view, "Cannot capture run", str(exc))
            return
        self.project.runs.append(run)
        self.changed()
        self.new_run()
        self.parent.statusBar().showMessage(
            f"Captured {run.label}. Review it in the Volume calculation tab."
        )

    def update(self):
        """Replace the run being edited while keeping its unique ID."""
        index = None
        for row, run in enumerate(self.project.runs):
            if run.uid == self.edit_uid:
                index = row
                break
        if index is None:
            return
        try:
            run = self._draft(self.edit_uid)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self.roi_view, "Cannot update run", str(exc))
            return
        self.project.runs[index] = run
        self.changed()
        self.parent.statusBar().showMessage(f"Saved edits to {run.label}.")

    def new_run(self):
        self.edit_uid = None
        view = self.roi_view
        view.update_button.setEnabled(False)
        view.capture_button.setEnabled(self.active_snapshot is not None)
        view.edit_label.setText("Draft — not captured")
        if self.active_snapshot:
            stem = Path(self.active_snapshot.source_path).stem
            number = 1
            while any(
                r.label == f"{stem} / expansion {number}" for r in self.project.runs
            ):
                number += 1
            view.label_edit.setText(f"{stem} / expansion {number}")

    def changed(self):
        self.dirty = True
        self.refresh_runs()
        self.fit_view.invalidate()

    def refresh_runs(self):
        from .models import PRESSURE_UNITS
        factor=1000/PRESSURE_UNITS[self.main.model.pressure_display_unit]
        table = self.fit_view.runs
        table.blockSignals(True)
        table.setRowCount(len(self.project.runs))
        for row, run in enumerate(self.project.runs):
            try:
                state = inputs_for_run(run, self.project.eos)
                pi, pf, ti, tf, status = (
                    state.pi_kpa,
                    state.pf_kpa,
                    state.ti_k - 273.15,
                    state.tf_k - 273.15,
                    "Ready",
                )
            except ValueError as exc:
                prefix = "Tu" if run.side == "Upstream" else "Td"
                added = run.barometric_kpa if run.pressure_basis == "Gauge" else 0.0
                pi = run.rois["Pi"].mean + added if added is not None else None
                pf = run.rois["Pf"].mean + added if added is not None else None
                ti, tf, status = (
                    run.rois[prefix + "_i"].mean,
                    run.rois[prefix + "_f"].mean,
                    str(exc),
                )
            values = [
                "",
                run.label,
                run.side,
                run.plug_id,
                run.plug_volume_cm3,
                pi*factor if pi is not None else None,
                pf*factor if pf is not None else None,
                ti,
                tf,
                status,
                run.exclusion_reason,
                Path(self.project.snapshots[run.dataset_id].source_path).name,
            ]
            for column, value in enumerate(values):
                text = (
                    f"{value:.7g}"
                    if isinstance(value, float)
                    else "—" if value is None else str(value)
                )
                cell = item(text, editable=column == 10)
                if column == 0:
                    cell.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    cell.setCheckState(
                        Qt.CheckState.Checked
                        if run.included
                        else Qt.CheckState.Unchecked
                    )
                cell.setToolTip(status if column == 9 else "\n".join(run_warnings(run)))
                table.setItem(row, column, cell)
        table.blockSignals(False)
        self.fit_view.collection_label.setText(
            f"{len(self.project.runs)} captured; {sum(r.included for r in self.project.runs)} included"
            + (" — unsaved changes" if self.dirty else "")
        )

    def run_cell_changed(self, row, column):
        if not 0 <= row < len(self.project.runs) or column not in (0, 10):
            return
        run = self.project.runs[row]
        if column == 0:
            run.included = (
                self.fit_view.runs.item(row, column).checkState()
                == Qt.CheckState.Checked
            )
        else:
            run.exclusion_reason = self.fit_view.runs.item(row, column).text().strip()
        if self.edit_uid == run.uid:
            self.roi_view.include.setChecked(run.included)
            self.roi_view.exclusion.setText(run.exclusion_reason)
        self.dirty = True
        self.fit_view.invalidate()
        self.fit_view.collection_label.setText(
            f"{len(self.project.runs)} captured; {sum(r.included for r in self.project.runs)} included — unsaved changes"
        )

    def edit_selected(self):
        row = self.fit_view.runs.currentRow()
        if not 0 <= row < len(self.project.runs):
            return
        run = self.project.runs[row]
        self.active_snapshot = self.project.snapshots[run.dataset_id]
        view = self.roi_view
        view.set_snapshot(self.active_snapshot)
        view._syncing = True
        for label, role in [("P", "Pi"), ("Tu", "Tu_i"), ("Td", "Td_i")]:
            combo = view.channel_combos[label]
            key = run.rois[role].channel_key if role in run.rois else None
            combo.setCurrentIndex(max(0, combo.findData(key)))
        view.side.setCurrentText(run.side)
        view.label_edit.setText(run.label)
        view.plug_id.setText(run.plug_id)
        view.configuration.setCurrentText(run.configuration)
        view.plug_volume.setValue(run.plug_volume_cm3)
        view.opening.setValue(run.opening_time_s)
        view.pressure_basis.setCurrentText(run.pressure_basis)
        view.barometric.set_canonical_value(run.barometric_kpa or 0)
        view.receiving_mode.setCurrentText(run.receiving_mode)
        view.receiving_pressure.set_canonical_value(run.receiving_pressure_kpa or 0)
        view.receiving_temperature.setValue(
            run.receiving_temperature_c
            if run.receiving_temperature_c is not None
            else -273.15
        )
        view.receiving_source.setText(run.receiving_temperature_source)
        view.final_confirmed.setChecked(run.final_temperature_confirmed)
        view.zi.setValue(run.zi_override or 0)
        view.zf.setValue(run.zf_override or 0)
        view.zb.setValue(run.zb)
        view.p_limit.set_canonical_value(run.pressure_drift_limit_kpa_min or 0)
        view.t_limit.setValue(run.temperature_drift_limit_c_min or 0)
        view.include.setChecked(run.included)
        view.exclusion.setText(run.exclusion_reason)
        view.notes.setText(run.notes)
        for role, roi in run.rois.items():
            for spin, value in zip(
                view.spins[role], (roi.requested_start_s, roi.requested_end_s)
            ):
                spin.setValue(value)
        view._syncing = False
        view.redraw()
        self.edit_uid = run.uid
        view.edit_label.setText(f"Editing: {run.label}")
        view.update_button.setEnabled(True)
        self.parent.tabs.setCurrentWidget(view)

    def remove_selected(self):
        row = self.fit_view.runs.currentRow()
        if 0 <= row < len(self.project.runs):
            run = self.project.runs.pop(row)
            self.removed.append((row, run))
            if self.edit_uid == run.uid:
                self.new_run()
            self.changed()

    def undo_remove(self):
        if self.removed:
            row, run = self.removed.pop()
            if any(r.label == run.label for r in self.project.runs):
                QMessageBox.warning(
                    self.fit_view,
                    "Cannot restore run",
                    "A run with this ID now exists. Rename it before restoring the removed run.",
                )
                self.removed.append((row, run))
                return
            self.project.runs.insert(min(row, len(self.project.runs)), run)
            self.changed()

    def set_eos(self, eos):
        if self.project.eos != eos:
            self.project.eos = eos
            self.changed()

    def calculate(self):
        """Call the independent volume solver, then send its result to the view."""
        try:
            result = fit_project_runs(self.project.runs, self.project.eos)
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.fit_view.invalidate(str(exc))
            return
        warnings = [
            f"{run.label}: {message}"
            for run in self.project.runs
            if run.included
            for message in run_warnings(run)
            if not message.startswith("Drift acceptance")
        ]
        if warnings:
            result.warnings.extend(warnings[:3])
            if len(warnings) > 3:
                result.warnings.append(
                    f"{len(warnings)-3} additional ROI warnings; inspect run tooltips or the Excel export."
                )
        if any(
            r.included
            and (
                r.pressure_drift_limit_kpa_min is None
                or r.temperature_drift_limit_c_min is None
            )
            for r in self.project.runs
        ):
            result.warnings.append(
                "Some drift acceptance limits are off. Review measured slopes; fit readiness does not certify equilibrium."
            )
        self.fit_view.show_fit(result)

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Save calibration session",
            str(self.session_path or "check_plug_session.json"),
            "Calibration session (*.json)",
        )
        if not filename:
            return False
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            save_session(self.project, path)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not save session", str(exc))
            return False
        self.session_path = path
        self.dirty = False
        self.refresh_runs()
        self.parent.statusBar().showMessage(f"Saved calibration session to {path}")
        return True

    def maybe_save(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self.parent,
            "Unsaved calibration changes",
            "Save the captured calibration runs and edits before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def new_session(self):
        if not self.maybe_save():
            return
        self.project = CalibrationProject()
        self.session_path = None
        self.dirty = False
        self.edit_uid = None
        self.removed.clear()
        self.fit_view.eos.blockSignals(True)
        self.fit_view.eos.setCurrentText(self.project.eos)
        self.fit_view.eos.blockSignals(False)
        view = self.roi_view
        view.pressure_basis.setCurrentText("Gauge")
        view.barometric.setValue(0)
        view.receiving_mode.setCurrentText("Vented")
        view.receiving_pressure.setValue(0)
        view.receiving_temperature.setValue(-273.15)
        view.receiving_source.clear()
        view.final_confirmed.setChecked(False)
        view.zi.setValue(0)
        view.zf.setValue(0)
        view.zb.setValue(1)
        view.p_limit.setValue(0)
        view.t_limit.setValue(0)
        view.include.setChecked(True)
        view.exclusion.clear()
        view.notes.clear()
        view.bore_id.setValue(0)
        view.bore_length.setValue(0)
        if self.main.model.data is not None:
            self.log_loaded(self.main.model)
        else:
            self.active_snapshot = None
            view.snapshot = None
            view.source_label.setText("Open a log to begin a new calibration session.")
            view.setEnabled(False)
        self.refresh_runs()
        self.fit_view.invalidate(
            "New calibration session. Capture experimental runs in Check-plug ROI."
        )
        self.parent.statusBar().showMessage(
            "Started a new calibration session; previous gas-law inputs were cleared."
        )

    def open_session(self):
        filename, _ = QFileDialog.getOpenFileName(
            self.parent, "Open calibration session", "", "Calibration session (*.json)"
        )
        if not filename:
            return
        try:
            project = load_session(filename)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not open session", str(exc))
            return
        if not self.maybe_save():
            return
        self.project = project
        self.session_path = Path(filename)
        self.dirty = False
        self.removed.clear()
        self.edit_uid = None
        self.fit_view.eos.blockSignals(True)
        self.fit_view.eos.setCurrentText(project.eos)
        self.fit_view.eos.blockSignals(False)
        self.refresh_runs()
        self.fit_view.invalidate(
            "Session opened. Select a run to edit, or calculate volumes."
        )
        if project.runs:
            self.fit_view.runs.selectRow(0)
            self.edit_selected()
        elif project.snapshots:
            self.active_snapshot = next(iter(project.snapshots.values()))
            self.roi_view.set_snapshot(self.active_snapshot)
            self.new_run()
        else:
            self.active_snapshot = None
            self.roi_view.snapshot = None
            self.roi_view.setEnabled(False)
        self.parent.statusBar().showMessage(
            f"Opened calibration session {filename}; raw samples are embedded."
        )

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Export check-plug statistics and fit",
            "check_plug_calibration.xlsx",
            "Excel workbook (*.xlsx)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")
        try:
            write_calibration_workbook(self.project, path)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not export calibration", str(exc))
            return
        self.parent.statusBar().showMessage(
            f"Exported calibration statistics, raw data and available fit to {path}"
        )
