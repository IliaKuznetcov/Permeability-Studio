"""Actions for paired pulse capture and selected-record permeability analysis."""

from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .calibration_view import item
from .state_extraction import snapshot_log, extract_roi
from .pulse_capture import capture_pulse
from .pulse_capture_models import PulseProject
from .pulse_capture_io import save_pulse_session, load_pulse_session, export_pulses
from .pulse_capture_view import PulseCaptureWidget


class PulseCaptureController:
    """Coordinate the Pulse picker widgets, pulse records, and file operations."""

    def __init__(self, main, parent):
        self.main, self.parent = main, parent
        self.project = PulseProject()
        self.active_snapshot = None
        self.edit_uid = None
        self.dirty = False
        self.session_path = None
        self.removed = []
        self.restoring = False
        self.view = PulseCaptureWidget(self)

    def log_loaded(self, model):
        """Keep a recording snapshot and display a fresh draft for that recording."""
        snapshot = snapshot_log(model)
        self.project.snapshots[snapshot.dataset_id] = snapshot
        self.active_snapshot = snapshot
        self.edit_uid = None
        self.view.set_snapshot(snapshot)
        self.new_pulse()

    def preview(self):
        """Show draft statistics and chronology errors without capturing a pulse."""
        if not self.active_snapshot:
            return
        view = self.view
        values = []
        errors = []
        for state in ("Initial", "Final"):
            for channel in self.active_snapshot.channels:
                if channel.quantity == "Pressure":
                    if state == "Initial":
                        key, role = "pi", "Pi"
                    else:
                        key, role = "pf", "Pf"
                else:
                    if state == "Initial":
                        key, role = "ti", "Ti"
                    else:
                        key, role = "tf", "Tf"
                try:
                    statistics = extract_roi(
                        self.active_snapshot, channel.key, view.bounds(key), role
                    )
                    values.append((state, statistics))
                except ValueError as exc:
                    errors.append(str(exc))

        regions_to_check = [("pi", "Initial pressure"), ("pf", "Final pressure")]
        has_temperature = any(
            channel.quantity == "Temperature"
            for channel in self.active_snapshot.channels
        )
        if has_temperature:
            regions_to_check.extend(
                [("ti", "Initial temperature"), ("tf", "Final temperature")]
            )
        for key, name in regions_to_check:
            bounds = view.bounds(key)
            if key in ("pi", "ti") and bounds[1] >= view.valve.value():
                errors.append(
                    f"{name} ends at {bounds[1]:g} s; valve opening is {view.valve.value():g} s. End this region before the valve event."
                )
            elif key in ("pf", "tf") and bounds[0] <= view.valve.value():
                errors.append(
                    f"{name} starts at {bounds[0]:g} s; valve opening is {view.valve.value():g} s. Start this region after the valve event."
                )
        view.show_statistics(values, errors)

    def analyze_selected(self):
        """Analyze a captured record, never an uncommitted draft."""
        row = self.view.pulses.currentRow()
        if not 0 <= row < len(self.project.pulses):
            QMessageBox.information(self.view, "Select a captured pulse", "Capture a pulse and select its row in the collection first.")
            return
        from .permeability_view import PermeabilityDialog
        dialog = PermeabilityDialog(self, self.project.pulses[row])
        dialog.exec()

    def draft(self, uid=None):
        """Read the form and build a candidate pulse; do not alter the collection."""
        if not self.active_snapshot:
            raise ValueError("Open a recording first.")
        view = self.view
        fit_bounds = None
        if view.custom_fit.isChecked():
            fit_bounds = (view.fit_start.value(), view.fit_end.value())
        pulse = capture_pulse(
            self.active_snapshot,
            label=view.pulse_id.text(),
            valve_time_s=view.valve.value(),
            initial_pressure_bounds=view.bounds("pi"),
            final_pressure_bounds=view.bounds("pf"),
            initial_temperature_bounds=view.bounds("ti"),
            final_temperature_bounds=view.bounds("tf"),
            fit_bounds=fit_bounds,
            pre_context_s=view.pre_context.value(),
            sample_id=view.sample_id.text(),
            notes=view.notes.text(),
            uid=uid,
        )
        if any(p.label == pulse.label and p.uid != uid for p in self.project.pulses):
            raise ValueError(
                "This pulse ID already exists. Use Save edits to pulse, or enter a different ID."
            )
        return pulse

    def capture(self):
        """Commit a valid draft to memory, then prepare the next pulse ID."""
        try:
            pulse = self.draft()
        except ValueError as exc:
            QMessageBox.warning(self.view, "Cannot capture pulse", str(exc))
            return
        self.project.pulses.append(pulse)
        self.changed()
        self.new_pulse()
        self.parent.statusBar().showMessage(
            f"Captured {pulse.label}: before/after states and transient referenced to valve t₀={pulse.valve_time_s:g} s."
        )

    def update(self):
        """Replace the pulse being edited while keeping its unique ID."""
        index = None
        for row, pulse in enumerate(self.project.pulses):
            if pulse.uid == self.edit_uid:
                index = row
                break
        if index is None:
            return
        try:
            pulse = self.draft(self.edit_uid)
        except ValueError as exc:
            QMessageBox.warning(self.view, "Cannot update pulse", str(exc))
            return
        self.project.pulses[index] = pulse
        self.changed()
        self.parent.statusBar().showMessage(f"Saved edits to {pulse.label}.")

    def new_pulse(self):
        self.edit_uid = None
        self.view.update_button.setEnabled(False)
        self.view.edit_label.setText("Draft — not captured")
        if self.active_snapshot:
            stem = Path(self.active_snapshot.source_path).stem
            number = 1
            while any(
                p.label == f"{stem} / pulse {number}" for p in self.project.pulses
            ):
                number += 1
            self.view.pulse_id.setText(f"{stem} / pulse {number}")

    def changed(self):
        """Mark in-memory changes as unsaved and refresh the collection table."""
        self.dirty = True
        self.refresh_collection()

    def refresh_collection(self):
        table = self.view.pulses
        table.setRowCount(len(self.project.pulses))
        for row, pulse in enumerate(self.project.pulses):
            values = [
                pulse.label,
                f"{pulse.valve_time_s:.6g}",
                f"{pulse.initial_pressure_bounds[0]:g}–{pulse.initial_pressure_bounds[1]:g}",
                f"{pulse.final_pressure_bounds[0]:g}–{pulse.final_pressure_bounds[1]:g}",
                f"{pulse.fit_start_s-pulse.valve_time_s:g}–{pulse.fit_end_s-pulse.valve_time_s:g}",
                Path(self.project.snapshots[pulse.dataset_id].source_path).name,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, item(value))
        self.view.collection_label.setText(
            f"{len(self.project.pulses)} captured pulses"
            + (" — unsaved changes" if self.dirty else "")
        )
        volumes = self.project.calibrated_volumes_cm3
        if volumes:
            volume_text = ", ".join(
                f"{name}={value:.6g}" for name, value in volumes.items()
            )
            self.view.volume_label.setText(
                "Calibrated reference: " + volume_text + " cm³"
            )
        else:
            self.view.volume_label.setText(
                "No calibrated volumes linked. Pulse extraction does not require them."
            )

    def edit_selected(self):
        """Restore a captured pulse's recording and controls into the edit form."""
        row = self.view.pulses.currentRow()
        if not 0 <= row < len(self.project.pulses):
            return
        pulse = self.project.pulses[row]
        self.active_snapshot = self.project.snapshots[pulse.dataset_id]
        view = self.view
        view.set_snapshot(self.active_snapshot)
        view.syncing = True
        for key, bounds in [
            ("pi", pulse.initial_pressure_bounds),
            ("pf", pulse.final_pressure_bounds),
            ("ti", pulse.initial_temperature_bounds),
            ("tf", pulse.final_temperature_bounds),
        ]:
            view.set_bounds(key, bounds)
        view.auto_valve.setChecked(False)
        view.valve.setValue(pulse.valve_time_s)
        linked = (
            pulse.initial_pressure_bounds == pulse.initial_temperature_bounds
            and pulse.final_pressure_bounds == pulse.final_temperature_bounds
        )
        view.link_temperature.setChecked(linked)
        transient_end = pulse.final_pressure_bounds[1]
        has_temperature = any(
            channel.quantity == "Temperature"
            for channel in self.active_snapshot.channels
        )
        if has_temperature:
            transient_end = max(transient_end, pulse.final_temperature_bounds[1])
        custom = (
            pulse.fit_start_s != pulse.valve_time_s or pulse.fit_end_s != transient_end
        )
        view.custom_fit.setChecked(custom)
        view.fit_start.setValue(pulse.fit_start_s)
        view.fit_end.setValue(pulse.fit_end_s)
        view.pre_context.setValue(pulse.pre_context_s)
        view.pulse_id.setText(pulse.label)
        view.sample_id.setText(pulse.sample_id)
        view.notes.setText(pulse.notes)
        view.syncing = False
        view.redraw()
        self.edit_uid = pulse.uid
        view.update_button.setEnabled(True)
        view.edit_label.setText(
            f"Editing: {pulse.label} — save edits to commit changes"
        )
        self.parent.tabs.setCurrentWidget(view)

    def remove_selected(self):
        row = self.view.pulses.currentRow()
        if not 0 <= row < len(self.project.pulses):
            return
        pulse = self.project.pulses.pop(row)
        self.removed.append((row, pulse))
        if pulse.uid == self.edit_uid:
            self.new_pulse()
        self.changed()

    def undo_remove(self):
        if not self.removed:
            return
        row, pulse = self.removed[-1]
        if any(p.label == pulse.label for p in self.project.pulses):
            QMessageBox.warning(
                self.view,
                "Cannot restore pulse",
                "A pulse with this ID now exists. Rename it before restoring.",
            )
            return
        self.removed.pop()
        self.project.pulses.insert(min(row, len(self.project.pulses)), pulse)
        self.changed()

    def settings_changed(self, *_args):
        if self.restoring:
            return
        self.project.pressure_basis = self.view.pressure_basis.currentText()
        self.project.barometric_kpa = self.view.barometric.canonical_value() or None
        self.view.pressure_plot.setLabel(
            "left", f"Pressure ({self.project.pressure_basis.lower()})", units=self.main.model.pressure_display_unit
        )
        self.changed()

    def import_calibration(self):
        """Copy a freshly calculated volume reference and its source conditions."""
        from .calibration import fit_project_runs

        calibration = self.main.calibration
        try:
            result = fit_project_runs(calibration.project.runs, calibration.project.eos)
        except ValueError as exc:
            QMessageBox.warning(
                self.view, "No calibrated reference available", str(exc)
            )
            return
        if any(value <= 0 for value in result.volumes_cm3.values()):
            QMessageBox.warning(
                self.view,
                "Review calibration",
                "The current calibration contains nonpositive volumes.",
            )
            return
        self.project.calibrated_volumes_cm3 = dict(result.volumes_cm3)
        self.project.calibration_provenance = {
            "source": str(
                calibration.session_path or "Current unsaved calibration collection"
            ),
            "copied_at_utc": datetime.now(timezone.utc).isoformat(),
            "eos": result.eos,
            "included_run_uids": result.uids,
            "sse": result.sse,
            "r_squared": result.r_squared,
            "warnings": result.warnings,
            "conditions": [
                {
                    "run": r.label,
                    "pressure_basis": r.pressure_basis,
                    "barometric_kpa": r.barometric_kpa,
                    "receiving_temperature_c": r.receiving_temperature_c,
                    "receiving_temperature_source": r.receiving_temperature_source,
                }
                for r in calibration.project.runs
                if r.included
            ],
        }
        self.changed()
        self.parent.statusBar().showMessage(
            "Copied the current calibrated volumes and provenance into the pulse session."
        )

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Save paired pulse session",
            str(self.session_path or "pulse_session.json"),
            "Pulse session (*.json)",
        )
        if not filename:
            return False
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            save_pulse_session(self.project, path)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not save pulses", str(exc))
            return False
        self.session_path = path
        self.dirty = False
        self.refresh_collection()
        self.parent.statusBar().showMessage(f"Saved paired pulse session to {path}")
        return True

    def maybe_save(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self.parent,
            "Unsaved paired pulses",
            "Save captured pulses and pulse-project settings before continuing?",
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

    def restore_project_settings(self):
        self.restoring = True
        self.view.pressure_basis.setCurrentText(self.project.pressure_basis)
        self.view.barometric.set_canonical_value(self.project.barometric_kpa or 0)
        self.restoring = False

    def open_session(self):
        filename, _ = QFileDialog.getOpenFileName(
            self.parent, "Open paired pulse session", "", "Pulse session (*.json)"
        )
        if not filename:
            return
        try:
            project = load_pulse_session(filename)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not open pulses", str(exc))
            return
        if not self.maybe_save():
            return
        self.project = project
        self.session_path = Path(filename)
        self.dirty = False
        self.removed.clear()
        self.edit_uid = None
        self.restore_project_settings()
        self.refresh_collection()
        if project.pulses:
            self.view.pulses.selectRow(0)
            self.edit_selected()
        elif project.snapshots:
            self.active_snapshot = next(iter(project.snapshots.values()))
            self.view.set_snapshot(self.active_snapshot)
            self.new_pulse()
        else:
            self.active_snapshot = None
            self.view.clear_draft()
        self.parent.statusBar().showMessage(
            "Opened paired pulse session; captured state statistics were recomputed from its embedded samples."
        )

    def new_session(self):
        if not self.maybe_save():
            return
        self.project = PulseProject()
        self.session_path = None
        self.dirty = False
        self.edit_uid = None
        self.removed.clear()
        self.restore_project_settings()
        self.refresh_collection()
        if self.main.model.data is not None:
            self.log_loaded(self.main.model)
        else:
            self.active_snapshot = None
            self.view.clear_draft()
        self.parent.statusBar().showMessage(
            "Started a new paired-pulse session. Calibration runs were retained."
        )

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Export paired pulse states and traces",
            "selected_pulses.xlsx",
            "Excel workbook (*.xlsx)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")
        try:
            export_pulses(self.project, path)
        except Exception as exc:
            QMessageBox.critical(self.parent, "Could not export pulses", str(exc))
            return
        self.parent.statusBar().showMessage(
            f"Exported paired states, full transient, context and fitting samples to {path}"
        )
