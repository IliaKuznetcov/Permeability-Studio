"""Paired before/after pulse selection with pressure/temperature plots."""

from __future__ import annotations
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from .calibration_view import number, item
from .core import display_values
from .pressure_widgets import PressureSpinBox
from .pressure_display import display_statistics

WINDOW_LABELS = {
    "pi": "Initial pressure",
    "pf": "Final pressure",
    "ti": "Initial temperature",
    "tf": "Final temperature",
}


class PulseCaptureWidget(QWidget):
    """Build the pulse form and keep its plotted/numeric controls in sync.

    Calculations and persistence are delegated to the controller. The `syncing`
    flag prevents a programmatic widget update from triggering another update.
    """

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.snapshot = None
        self.syncing = False
        self.regions = {}
        self.time_spins = {}

        # Top-level layout: file controls above the left/right work areas.
        layout = QVBoxLayout(self)
        self.source_label = QLabel(
            "Open a recording, then select before/after regions around one pulse. Captures remain when you open another log."
        )
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        toolbar = QHBoxLayout()
        for text, slot in [
            ("Open log…", controller.main.open_log),
            ("Save pulses…", controller.save),
            ("Open pulses…", controller.open_session),
            ("New pulse session", controller.new_session),
            ("Export pulses…", controller.export),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Left side: region controls, valve marker, plots, bounds and capture.
        controls = QHBoxLayout()
        self.target = QComboBox()
        for key, label in WINDOW_LABELS.items():
            self.target.addItem(label, key)
        self.target.addItem("Valve opening", "valve")
        self.target.currentIndexChanged.connect(self.activate_target)
        controls.addWidget(QLabel("Click/drag:"))
        controls.addWidget(self.target)
        self.width = number(0.001, 1e9, 30, 3)
        self.width.setSuffix(" s")
        controls.addWidget(QLabel("ROI width:"))
        controls.addWidget(self.width)
        self.around_button = QPushButton("Place regions around valve")
        self.around_button.clicked.connect(self.place_around_valve)
        controls.addWidget(self.around_button)
        controls.addStretch(1)
        left_layout.addLayout(controls)
        timing = QHBoxLayout()
        self.valve = number(0, 1e10, 0, 6)
        self.valve.setSuffix(" s")
        self.valve.valueChanged.connect(self.valve_spin_changed)
        timing.addWidget(QLabel("Valve opening (t=0):"))
        timing.addWidget(self.valve)
        self.auto_valve = QCheckBox("Suggest marker between pressure regions")
        self.auto_valve.setChecked(True)
        self.auto_valve.toggled.connect(self.update_marker_from_windows)
        timing.addWidget(self.auto_valve)
        timing.addStretch(1)
        left_layout.addLayout(timing)
        plot_split = QSplitter(Qt.Orientation.Vertical)
        self.pressure_plot = pg.PlotWidget()
        self.temperature_plot = pg.PlotWidget()
        for plot in (self.pressure_plot, self.temperature_plot):
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.setLabel("bottom", "Elapsed time in recording", units="s")
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.addLegend()
            # Keep a reference to this specific plot in the callback. Without
            # p=plot, both callbacks would use the last plot in the loop.
            plot.scene().sigMouseClicked.connect(
                lambda event, p=plot: self.plot_clicked(event, p)
            )
            plot_split.addWidget(plot)
        self.temperature_plot.setXLink(self.pressure_plot)
        self.temperature_plot.setLabel("left", "Temperature", units="degC")
        left_layout.addWidget(plot_split, 4)
        window_table = QTableWidget(4, 3)
        window_table.setHorizontalHeaderLabels(
            ["Region", "Start elapsed (s)", "End elapsed (s)"]
        )
        window_table.verticalHeader().hide()
        window_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        window_table.setMinimumHeight(145)
        window_table.setMaximumHeight(155)
        for row, (key, label) in enumerate(WINDOW_LABELS.items()):
            window_table.setItem(row, 0, item(label))
            spins = []
            for col in (1, 2):
                spin = number(0, 1e10, 0, 6)
                spin.valueChanged.connect(
                    lambda _value, k=key: self.window_spin_changed(k)
                )
                window_table.setCellWidget(row, col, spin)
                spins.append(spin)
            self.time_spins[key] = spins
        left_layout.addWidget(window_table)
        links = QHBoxLayout()
        self.link_temperature = QCheckBox("Link temperature to pressure regions")
        self.link_temperature.setChecked(True)
        self.link_temperature.toggled.connect(self.temperature_link_changed)
        links.addWidget(self.link_temperature)
        self.custom_fit = QCheckBox("Choose separate fitting interval")
        self.custom_fit.toggled.connect(self.fit_changed)
        links.addWidget(self.custom_fit)
        self.fit_start = number(0, 1e10, 0, 6)
        self.fit_end = number(0, 1e10, 1, 6)
        self.fit_start.valueChanged.connect(self.fit_changed)
        self.fit_end.valueChanged.connect(self.fit_changed)
        links.addWidget(QLabel("Fit start:"))
        links.addWidget(self.fit_start)
        links.addWidget(QLabel("End:"))
        links.addWidget(self.fit_end)
        left_layout.addLayout(links)
        action_row = QHBoxLayout()
        self.pre_context = number(0, 1e8, 120, 2)
        self.pre_context.setSuffix(" s")
        action_row.addWidget(QLabel("Context before valve:"))
        action_row.addWidget(self.pre_context)
        self.new_button = QPushButton("New pulse")
        self.new_button.clicked.connect(controller.new_pulse)
        self.capture_button = QPushButton("Capture pulse")
        self.capture_button.clicked.connect(controller.capture)
        self.update_button = QPushButton("Save edits to pulse")
        self.update_button.clicked.connect(controller.update)
        for button in (self.new_button, self.capture_button, self.update_button):
            action_row.addWidget(button)
        left_layout.addLayout(action_row)
        self.status = QLabel(
            "Blue = before; green = after; dashed orange = valve opening. Suggested marker is not automatic event detection."
        )
        self.status.setWordWrap(True)
        left_layout.addWidget(self.status)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Right side: pulse identity, pressure setup, collection and statistics.
        self.edit_label = QLabel("Draft — not captured")
        self.edit_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(self.edit_label)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.pulse_id = QLineEdit()
        self.sample_id = QLineEdit()
        self.notes = QLineEdit()
        self.pressure_basis = QComboBox()
        self.pressure_basis.addItems(["Gauge", "Absolute"])
        self.barometric = PressureSpinBox(0, 1e5, 0, 6, "Not entered")
        self.barometric.set_pressure_unit(controller.main.model.pressure_display_unit)
        form.addRow("Pulse ID", self.pulse_id)
        form.addRow("Sample ID", self.sample_id)
        form.addRow("Notes", self.notes)
        form.addRow("Pressure basis (all pulses)", self.pressure_basis)
        form.addRow("Measured barometric P", self.barometric)
        right_layout.addLayout(form)
        self.pressure_basis.currentTextChanged.connect(controller.settings_changed)
        self.barometric.valueChanged.connect(controller.settings_changed)
        volume_button = QPushButton("Use current calibrated volumes")
        volume_button.clicked.connect(controller.import_calibration)
        right_layout.addWidget(volume_button)
        self.volume_label = QLabel(
            "No calibrated volumes linked. Pulse extraction does not require them."
        )
        self.volume_label.setWordWrap(True)
        right_layout.addWidget(self.volume_label)
        self.collection_label = QLabel("0 captured pulses")
        right_layout.addWidget(self.collection_label)
        self.pulses = QTableWidget(0, 6)
        self.pulses.setHorizontalHeaderLabels(
            [
                "Pulse ID",
                "Valve t₀ (s)",
                "Initial P (s)",
                "Final P (s)",
                "Fit (s from valve)",
                "Source",
            ]
        )
        self.pulses.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pulses.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pulses.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.pulses.itemDoubleClicked.connect(lambda *_: controller.edit_selected())
        right_layout.addWidget(self.pulses, 2)
        edit_row = QHBoxLayout()
        for text, slot in [
            ("Edit selected", controller.edit_selected),
            ("Remove pulse", controller.remove_selected),
            ("Undo removal", controller.undo_remove),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            edit_row.addWidget(button)
        right_layout.addLayout(edit_row)
        self.analyze_button = QPushButton("Analyze selected pulse: late time / FDM…")
        self.analyze_button.clicked.connect(controller.analyze_selected)
        right_layout.addWidget(self.analyze_button)
        self.statistics_label=QLabel('Current draft state statistics; hover for details')
        right_layout.addWidget(self.statistics_label)
        self.statistics = QTableWidget(0, 7)
        self.statistics.setHorizontalHeaderLabels(
            ["State", "Channel", "Mean", "SD", "n", "Slope/min", "Unit"]
        )
        self.statistics.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.statistics.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self.statistics, 2)
        fitting_note = QLabel(
            "Select a captured pulse to compare late-time permeability with FDM history matching. Original samples remain referenced to valve t₀."
        )
        fitting_note.setWordWrap(True)
        right_layout.addWidget(fitting_note)
        splitter.addWidget(right)
        splitter.setSizes([1000, 500])
        self.draft_controls = [
            self.target,
            self.width,
            self.around_button,
            self.valve,
            self.auto_valve,
            window_table,
            self.link_temperature,
            self.custom_fit,
            self.pre_context,
            self.new_button,
            self.capture_button,
        ]
        self.set_draft_enabled(False)

    def clear_draft(self):
        self.snapshot = None
        self.regions = {}
        self.lines = []
        self.fit_regions = []
        for plot in (self.pressure_plot, self.temperature_plot):
            plot.clear()
            plot.plotItem.legend.clear()
        self.statistics.setRowCount(0)
        self.pulse_id.clear()
        self.sample_id.clear()
        self.notes.clear()
        self.edit_label.setText("Draft — no recording loaded")
        self.source_label.setText(
            "Open a recording to select before/after pulse regions."
        )
        self.set_draft_enabled(False)

    def set_draft_enabled(self, enabled):
        for widget in self.draft_controls:
            widget.setEnabled(enabled)
        self.update_button.setEnabled(enabled and self.controller.edit_uid is not None)
        self.fit_start.setEnabled(enabled and self.custom_fit.isChecked())
        self.fit_end.setEnabled(enabled and self.custom_fit.isChecked())

    def bounds(self, key):
        return tuple(s.value() for s in self.time_spins[key])

    def set_bounds(self, key, bounds):
        old = self.syncing
        self.syncing = True
        for spin, value in zip(self.time_spins[key], sorted(bounds)):
            spin.setValue(value)
        if key in self.regions:
            self.regions[key].setRegion(sorted(bounds))
        self.syncing = old

    def set_snapshot(self, snapshot, restore=False):
        self.snapshot = snapshot
        self.syncing = True
        self.set_draft_enabled(True)
        self.source_label.setText(
            f"{snapshot.source_path} — {len(snapshot.data):,} samples. Select before/after states around the same valve event."
        )
        first_time, last_time = float(snapshot.data.Time.iloc[0]), float(
            snapshot.data.Time.iloc[-1]
        )
        width = min(30.0, (last_time - first_time) * 0.1)
        for key, spins in self.time_spins.items():
            for spin in spins:
                spin.setRange(first_time, last_time)
            if not restore:
                self.set_bounds(
                    key,
                    (
                        (first_time, first_time + width)
                        if key in ("pi", "ti")
                        else (last_time - width, last_time)
                    ),
                )
        self.valve.setRange(first_time, last_time)
        self.fit_start.setRange(first_time, last_time)
        self.fit_end.setRange(first_time, last_time)
        if not restore:
            self.valve.setValue((first_time + last_time) / 2)
            self.auto_valve.setChecked(True)
            self.link_temperature.setChecked(True)
            self.custom_fit.setChecked(False)
        self.syncing = False
        self.redraw()

    def redraw(self):
        """Rebuild curves, state regions, valve lines and the optional fit overlay."""
        if not self.snapshot:
            return
        self.syncing = True
        self.regions = {}
        for plot in (self.pressure_plot, self.temperature_plot):
            plot.clear()
            plot.plotItem.legend.clear()
        times = self.snapshot.data.Time.to_numpy(float)
        pressure_color_index = temperature_color_index = 0
        for channel in self.snapshot.channels:
            pressure = channel.quantity == "Pressure"
            if pressure:
                plot = self.pressure_plot
                unit = self.controller.main.model.pressure_display_unit
                color = ["#71CF88", "#55B4EF", "#D9A5F5", "#E3C655"][
                    pressure_color_index % 4
                ]
            else:
                plot = self.temperature_plot
                unit = "degC"
                color = ["#F1AA54", "#F0768A", "#CCCCCC"][temperature_color_index % 3]
            curve = plot.plot(
                times,
                display_values(
                    self.snapshot.data[channel.key].to_numpy(float), channel, unit
                ),
                pen=pg.mkPen(color, width=1.2),
                connect="finite",
                name=channel.source_column,
            )
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            if pressure:
                pressure_color_index += 1
            else:
                temperature_color_index += 1
        self.pressure_plot.setLabel(
            "left",
            f"Pressure ({self.pressure_basis.currentText().lower()})",
            units=self.controller.main.model.pressure_display_unit,
        )
        self.has_temperature = temperature_color_index > 0
        for key in WINDOW_LABELS:
            if key in ("ti", "tf") and not self.has_temperature:
                continue
            plot = self.pressure_plot if key in ("pi", "pf") else self.temperature_plot
            initial = key in ("pi", "ti")
            color = (70, 150, 240) if initial else (75, 200, 130)
            region = pg.LinearRegionItem(
                self.bounds(key),
                bounds=(times[0], times[-1]),
                swapMode="sort",
                brush=pg.mkBrush(*color, 45),
                pen=pg.mkPen(*color, width=1.6),
            )
            plot.addItem(region)
            self.regions[key] = region
            region.sigRegionChanged.connect(
                lambda _region, k=key: self.region_changed(k)
            )
        self.lines = []
        for plot in (self.pressure_plot, self.temperature_plot):
            line = pg.InfiniteLine(
                self.valve.value(),
                bounds=(times[0], times[-1]),
                movable=True,
                pen=pg.mkPen("#FFA534", width=2, style=Qt.PenStyle.DashLine),
            )
            line.setZValue(40)
            line.sigPositionChanged.connect(self.valve_line_changed)
            plot.addItem(line)
            self.lines.append(line)
        self.fit_regions = []
        for plot in (self.pressure_plot, self.temperature_plot):
            fit = pg.LinearRegionItem(
                (self.fit_start.value(), self.fit_end.value()),
                movable=False,
                brush=pg.mkBrush(185, 140, 230, 15),
                pen=pg.mkPen("#A478C2"),
            )
            fit.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for line in fit.lines:
                line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            fit.setZValue(1)
            plot.addItem(fit)
            self.fit_regions.append(fit)
        self.pressure_plot.autoRange()
        self.temperature_plot.autoRange()
        self.pressure_plot.setXRange(float(times[0]), float(times[-1]), padding=0)
        self.syncing = False
        self.temperature_link_changed()
        self.activate_target()
        self.fit_changed()
        self.controller.preview()

    def activate_target(self, *_args):
        active = self.target.currentData()
        for key, region in self.regions.items():
            selected = key == active and not (
                key in ("ti", "tf") and self.link_temperature.isChecked()
            )
            region.setMovable(selected)
            region.setZValue(25 if selected else 5)
            region.setAcceptedMouseButtons(
                Qt.MouseButton.LeftButton if selected else Qt.MouseButton.NoButton
            )
            for line in region.lines:
                line.setAcceptedMouseButtons(
                    Qt.MouseButton.LeftButton if selected else Qt.MouseButton.NoButton
                )

    def window_spin_changed(self, key):
        if self.syncing:
            return
        self.set_bounds(key, self.bounds(key))
        self.after_window_change(key)

    def region_changed(self, key):
        if self.syncing:
            return
        self.set_bounds(key, self.regions[key].getRegion())
        self.after_window_change(key)

    def after_window_change(self, key):
        if self.link_temperature.isChecked() and key in ("pi", "pf"):
            self.set_bounds("ti" if key == "pi" else "tf", self.bounds(key))
        if key in ("pi", "pf"):
            self.update_marker_from_windows()
        self.fit_changed()
        self.controller.preview()

    def temperature_link_changed(self, *_args):
        if self.syncing:
            return
        linked = self.link_temperature.isChecked()
        if linked:
            self.set_bounds("ti", self.bounds("pi"))
            self.set_bounds("tf", self.bounds("pf"))
        for key in ("ti", "tf"):
            for spin in self.time_spins[key]:
                spin.setEnabled(
                    not linked and bool(getattr(self, "has_temperature", False))
                )
        self.activate_target()
        self.fit_changed()
        self.controller.preview()

    def update_marker_from_windows(self, *_args):
        if self.syncing or not self.snapshot or not self.auto_valve.isChecked():
            return
        left, right = self.bounds("pi")[1], self.bounds("pf")[0]
        if left < right:
            self.syncing = True
            self.valve.setValue((left + right) / 2)
            for line in getattr(self, "lines", []):
                line.setValue(self.valve.value())
            self.syncing = False
        self.fit_changed()
        self.controller.preview()

    def valve_spin_changed(self, *_args):
        if self.syncing:
            return
        self.auto_valve.blockSignals(True)
        self.auto_valve.setChecked(False)
        self.auto_valve.blockSignals(False)
        self.syncing = True
        for line in getattr(self, "lines", []):
            line.setValue(self.valve.value())
        self.syncing = False
        self.fit_changed()
        self.controller.preview()

    def valve_line_changed(self, line):
        if self.syncing:
            return
        self.valve.setValue(float(line.value()))

    def plot_clicked(self, event, plot):
        if (
            not self.snapshot
            or event.button() != Qt.MouseButton.LeftButton
            or event.isAccepted()
        ):
            return
        view_box = plot.plotItem.vb
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        key = self.target.currentData()
        center = float(view_box.mapSceneToView(event.scenePos()).x())
        if key == "valve":
            self.valve.setValue(center)
            return
        if key in ("ti", "tf") and self.link_temperature.isChecked():
            self.status.setText(
                "Uncheck Link temperature to pressure regions to position a separate temperature ROI."
            )
            return
        expected = self.pressure_plot if key in ("pi", "pf") else self.temperature_plot
        if plot is not expected or key not in self.regions:
            return
        first_time, last_time = float(self.snapshot.data.Time.iloc[0]), float(
            self.snapshot.data.Time.iloc[-1]
        )
        width = min(self.width.value(), last_time - first_time)
        left = max(first_time, min(center - width / 2, last_time - width))
        self.set_bounds(key, (left, left + width))
        self.after_window_change(key)

    def place_around_valve(self):
        if not self.snapshot:
            return
        valve, width = self.valve.value(), self.width.value()
        first_time, last_time = float(self.snapshot.data.Time.iloc[0]), float(
            self.snapshot.data.Time.iloc[-1]
        )
        gap = min(5.0, width * 0.1)
        if valve - gap <= first_time or valve + gap >= last_time:
            self.status.setText(
                "Move the valve marker inside the recording before placing before/after regions."
            )
            return
        self.set_bounds("pi", (max(first_time, valve - gap - width), valve - gap))
        self.set_bounds("pf", (valve + gap, min(last_time, valve + gap + width)))
        self.temperature_link_changed()
        self.update_marker_from_windows()
        self.fit_changed()
        self.controller.preview()

    def fit_changed(self, *_args):
        """Update the fit overlay; the default covers the whole saved transient."""
        if self.syncing:
            return
        custom = self.custom_fit.isChecked()
        self.fit_start.setEnabled(self.snapshot is not None and custom)
        self.fit_end.setEnabled(self.snapshot is not None and custom)
        if not custom:
            self.syncing = True
            self.fit_start.setValue(self.valve.value())
            transient_end = self.bounds("pf")[1]
            if getattr(self, "has_temperature", False):
                transient_end = max(transient_end, self.bounds("tf")[1])
            self.fit_end.setValue(transient_end)
            self.syncing = False
        for region in getattr(self, "fit_regions", []):
            region.setRegion((self.fit_start.value(), self.fit_end.value()))
            region.setVisible(custom)

    def show_statistics(self, values, errors):
        unit=self.controller.main.model.pressure_display_unit
        self.statistics_label.setText(f'Current draft state statistics — {unit} / °C; hover for details')
        self.statistics.setRowCount(len(values))
        for row, (state, stat) in enumerate(values):
            stat=display_statistics(stat,unit)
            texts = [
                state,
                stat.source_column,
                f"{stat.mean:.7g}",
                f"{stat.std:.4g}",
                stat.count,
                f"{stat.slope_per_min:.4g}",
                stat.unit,
            ]
            detail = (
                f"Original {stat.source_unit}; valid {stat.count}/{stat.total_count}; "
                f"min {stat.minimum:.7g}, max {stat.maximum:.7g}; max gap {stat.max_time_gap_s:g} s. "
                f"Sample times {stat.start_s:g}–{stat.end_s:g} s; source rows {stat.start_source_row}–{stat.end_source_row}."
            )
            for col, value in enumerate(texts):
                cell = item(value)
                cell.setToolTip(detail)
                self.statistics.setItem(row, col, cell)
        self.status.setText(
            "\n".join(errors)
            if errors
            else f"Valve t₀ = {self.valve.value():g} s. Capture retains both states and every transient sample; no permeability fit has been run."
        )
