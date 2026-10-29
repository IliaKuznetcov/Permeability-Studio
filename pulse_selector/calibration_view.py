"""Widgets for manual ROI extraction and volume fitting; calculations live elsewhere."""

from __future__ import annotations
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .calibration_models import ROI_ROLES, CONFIGURATIONS
from .calibration import EOS_OPTIONS
from .core import display_values
from .models import PRESSURE_UNITS
from .pressure_widgets import PressureSpinBox
from .pressure_display import display_statistics

ROLE_LABELS = {
    "Pi": "Initial pressure",
    "Pf": "Final pressure",
    "Tu_i": "Initial Tu",
    "Tu_f": "Final Tu",
    "Td_i": "Initial Td",
    "Td_f": "Final Td",
}


def number(low, high, value=0.0, decimals=5, special=None):
    widget = QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    if special:
        widget.setSpecialValueText(special)
    return widget


def item(text, editable=False):
    result = QTableWidgetItem(str(text))
    if not editable:
        result.setFlags(result.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return result


class CalibrationROIWidget(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.snapshot = None
        self._syncing = False
        self.regions = {}
        self.spins = {}
        self.channel_combos = {}
        layout = QVBoxLayout(self)
        self.source_label = QLabel(
            "Open a log to select calibration regions. Captured runs survive opening another log."
        )
        self.source_label.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.source_label, 1)
        open_button = QPushButton("Open log…")
        open_button.clicked.connect(controller.main.open_log)
        header.addWidget(open_button)
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        selectors = QHBoxLayout()
        self.target = QComboBox()
        for role in ROI_ROLES:
            self.target.addItem(ROLE_LABELS[role], role)
        self.target.currentIndexChanged.connect(self._activate_region)
        self.click_width = number(0.001, 1e7, 30, 3)
        self.click_width.setSuffix(" s")
        selectors.addWidget(QLabel("Click/drag ROI:"))
        selectors.addWidget(self.target)
        selectors.addWidget(QLabel("Click width:"))
        selectors.addWidget(self.click_width)
        selectors.addStretch(1)
        left_layout.addLayout(selectors)
        plots = QSplitter(Qt.Orientation.Vertical)
        self.pressure_plot = pg.PlotWidget()
        self.temperature_plot = pg.PlotWidget()
        for plot in (self.pressure_plot, self.temperature_plot):
            plot.showGrid(x=True, y=True, alpha=0.18)
            plot.setLabel("bottom", "Elapsed time", units="s")
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.scene().sigMouseClicked.connect(
                lambda event, p=plot: self._plot_clicked(event, p)
            )
            plots.addWidget(plot)
        self.pressure_plot.setLabel("left", "Pressure", units="kPa")
        self.temperature_plot.setLabel("left", "Temperature", units="degC")
        self.temperature_plot.setXLink(self.pressure_plot)
        left_layout.addWidget(plots, 3)
        self.roi_table = QTableWidget(6, 8)
        self.roi_table.setHorizontalHeaderLabels(
            [
                "ROI",
                "Start (s)",
                "End (s)",
                "Mean",
                "Sample SD",
                "n",
                "Slope/min",
                "Unit",
            ]
        )
        self.roi_table.setMaximumHeight(220)
        self.roi_table.setMinimumHeight(190)
        self.roi_table.verticalHeader().hide()
        self.roi_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.roi_table.horizontalHeader().setStretchLastSection(True)
        for row, role in enumerate(ROI_ROLES):
            self.roi_table.setItem(row, 0, item(ROLE_LABELS[role]))
            controls = []
            for column in (1, 2):
                spin = number(0, 1e10, 0, 6)
                spin.setEnabled(False)
                spin.setMinimumWidth(95)
                spin.valueChanged.connect(lambda _value, r=role: self._spin_changed(r))
                self.roi_table.setCellWidget(row, column, spin)
                controls.append(spin)
            self.spins[role] = controls
        left_layout.addWidget(self.roi_table)
        buttons = QHBoxLayout()
        match = QPushButton("Match temperature windows to pressure")
        match.clicked.connect(self.match_temperature_windows)
        buttons.addWidget(match)
        self.new_button = QPushButton("New run")
        self.new_button.clicked.connect(controller.new_run)
        buttons.addWidget(self.new_button)
        self.capture_button = QPushButton("Capture new run")
        self.capture_button.clicked.connect(controller.capture)
        buttons.addWidget(self.capture_button)
        self.update_button = QPushButton("Save edits to run")
        self.update_button.clicked.connect(controller.update)
        self.update_button.setEnabled(False)
        buttons.addWidget(self.update_button)
        left_layout.addLayout(buttons)
        self.preview_label = QLabel(
            "Blue = initial, green = final, dashed line = expansion time. Select stable windows manually."
        )
        self.preview_label.setWordWrap(True)
        left_layout.addWidget(self.preview_label)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.edit_label = QLabel("Draft — not captured")
        self.edit_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(self.edit_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.label_edit = QLineEdit()
        self.side = QComboBox()
        self.side.addItems(["Upstream", "Downstream"])
        self.plug_id = QLineEdit()
        self.configuration = QComboBox()
        self.configuration.addItems(CONFIGURATIONS)
        self.plug_volume = number(0, 1e7, 0, 8)
        self.plug_volume.setSuffix(" cm³")
        self.bore_id = number(0, 1e5, 0, 4)
        self.bore_id.setSuffix(" mm")
        self.bore_length = number(0, 1e5, 0, 4)
        self.bore_length.setSuffix(" mm")
        geometry = QPushButton("Calculate Vp from bore ID and length")
        geometry.clicked.connect(controller.calculate_geometry)
        self.opening = number(0, 1e10, 0, 6)
        self.opening.setSuffix(" s")
        self.opening.valueChanged.connect(self._opening_changed)
        form.addRow("Run ID", self.label_edit)
        form.addRow("Charged tank", self.side)
        form.addRow("Plug ID", self.plug_id)
        form.addRow("Configuration", self.configuration)
        form.addRow("Known plug volume Vp", self.plug_volume)
        form.addRow("Bore ID (optional)", self.bore_id)
        form.addRow("Bore length (optional)", self.bore_length)
        form.addRow(geometry)
        form.addRow("Expansion time", self.opening)
        for role, label in [
            ("P", "Tank pressure channel"),
            ("Tu", "Upstream tank TC"),
            ("Td", "Downstream tank TC"),
        ]:
            combo = QComboBox()
            combo.addItem("<unassigned>", None)
            combo.currentIndexChanged.connect(self._channels_changed)
            self.channel_combos[role] = combo
            form.addRow(label, combo)
        self.pressure_basis = QComboBox()
        self.pressure_basis.addItems(["Gauge", "Absolute"])
        self.pressure_basis.currentTextChanged.connect(
            lambda text: self.pressure_plot.setLabel(
                "left", f"Pressure ({text.lower()})", units=self.controller.main.model.pressure_display_unit
            )
        )
        self.barometric = PressureSpinBox(0, 1e5, 0, 6, "Not entered")
        self.receiving_mode = QComboBox()
        self.receiving_mode.addItems(["Vented", "Measured absolute"])
        self.receiving_pressure = PressureSpinBox(0, 1e8, 0, 6)
        self.receiving_temperature = number(-273.15, 1e4, -273.15, 6, "Not entered")
        self.receiving_temperature.setSuffix(" °C")
        self.receiving_source = QLineEdit()
        self.receiving_source.setPlaceholderText(
            "Measurement or stated equilibrium assumption"
        )
        self.final_confirmed = QCheckBox("Use tank Tf as common final T")
        self.final_confirmed.setToolTip(
            "The solver assumes a common final temperature. Confirm this after reviewing thermal stabilization."
        )
        self.zi = number(0, 1e4, 0, 8, "Calculate from EOS")
        self.zf = number(0, 1e4, 0, 8, "Calculate from EOS")
        self.zb = number(0.000001, 1e4, 1, 8)
        self.p_limit = PressureSpinBox(0, 1e6, 0, 6, "Off",qualifier='/min')
        for spin in (self.barometric,self.receiving_pressure,self.p_limit):
            spin.set_pressure_unit(controller.main.model.pressure_display_unit)
        self.t_limit = number(0, 1e4, 0, 6, "Off")
        self.t_limit.setSuffix(" °C/min")
        self.include = QCheckBox("Include in fit")
        self.include.setChecked(True)
        self.exclusion = QLineEdit()
        self.notes = QLineEdit()
        for label, widget in [
            ("Pressure basis", self.pressure_basis),
            ("Measured barometric P", self.barometric),
            ("Initial receiving state", self.receiving_mode),
            ("Measured receiving Pb", self.receiving_pressure),
            ("Receiving temperature Tb", self.receiving_temperature),
            ("Tb source / assumption", self.receiving_source),
            ("Final temperature", self.final_confirmed),
            ("Zi override", self.zi),
            ("Zf override", self.zf),
            ("Receiving Zb", self.zb),
            ("Pressure drift limit", self.p_limit),
            ("Temperature drift limit", self.t_limit),
            ("Run inclusion", self.include),
            ("Exclusion reason", self.exclusion),
            ("Notes", self.notes),
        ]:
            form.addRow(label, widget)
        notice = QLabel(
            "Tu/Td measure their tanks. Tb is a separate receiving-volume input. Missing gas-law inputs may be filled later by editing a captured run."
        )
        notice.setWordWrap(True)
        form.addRow(notice)
        scroll.setWidget(form_widget)
        right_layout.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setSizes([1000, 440])
        self.side.currentTextChanged.connect(controller.side_changed)
        self.configuration.currentTextChanged.connect(self._configuration_changed)
        self.receiving_mode.currentTextChanged.connect(self._receiving_mode_changed)
        self._receiving_mode_changed()
        self.setEnabled(False)

    def _configuration_changed(self, *_args):
        solid = self.configuration.currentText() == "Solid isolating"
        self.plug_volume.setEnabled(not solid)
        if solid:
            self.plug_volume.setValue(0)

    def _receiving_mode_changed(self, *_args):
        self.receiving_pressure.setEnabled(
            self.receiving_mode.currentText() == "Measured absolute"
        )

    def set_snapshot(self, snapshot):
        self._syncing = True
        self.snapshot = snapshot
        self.setEnabled(True)
        self.source_label.setText(
            f"{snapshot.source_path} — {len(snapshot.data):,} samples. Runs from other logs remain in the calculation tab."
        )
        for role, combo in self.channel_combos.items():
            combo.clear()
            combo.addItem("<unassigned>", None)
            quantity = "Pressure" if role == "P" else "Temperature"
            for c in snapshot.channels:
                if c.quantity == quantity:
                    combo.addItem(f"{c.source_column} [{c.unit}]", c.key)
            if role == "P" and combo.count() > 1:
                combo.setCurrentIndex(1)
            else:
                location = "Upstream" if role == "Tu" else "Downstream"
                match = next(
                    (
                        c.key
                        for c in snapshot.channels
                        if c.quantity == quantity and c.location == location
                    ),
                    None,
                )
                if match:
                    combo.setCurrentIndex(combo.findData(match))
        lo, hi = float(snapshot.data["Time"].iloc[0]), float(
            snapshot.data["Time"].iloc[-1]
        )
        width = min(30.0, (hi - lo) * 0.15)
        for role, spins in self.spins.items():
            for spin in spins:
                spin.setRange(lo, hi)
            bounds = (
                (lo, lo + width) if role in ("Pi", "Tu_i", "Td_i") else (hi - width, hi)
            )
            for spin, value in zip(spins, bounds):
                spin.setValue(value)
        self.opening.setRange(lo, hi)
        self.opening.setValue((lo + hi) / 2)
        self._syncing = False
        self.redraw()

    def channel_roles(self):
        return {
            role: self.channel_combos[
                "P" if role in ("Pi", "Pf") else role[:2]
            ].currentData()
            for role in ROI_ROLES
        }

    def bounds(self):
        return {
            role: tuple(s.value() for s in spins) for role, spins in self.spins.items()
        }

    def set_bounds(self, role, bounds):
        self._syncing = True
        for spin, value in zip(self.spins[role], bounds):
            spin.setValue(value)
        if role in self.regions:
            self.regions[role].setRegion(bounds)
        self._syncing = False

    def _channels_changed(self, *_args):
        if not self._syncing and self.snapshot:
            self.redraw()

    def redraw(self):
        if not self.snapshot:
            return
        self._syncing = True
        self.pressure_plot.clear()
        self.temperature_plot.clear()
        self.pressure_plot.setLabel(
            "left",
            f"Pressure ({self.pressure_basis.currentText().lower()})",
            units=self.controller.main.model.pressure_display_unit,
        )
        old = self.temperature_plot.plotItem.legend
        if old is not None:
            old.clear()
        else:
            self.temperature_plot.addLegend()
        self.regions = {}
        times = self.snapshot.data["Time"].to_numpy(float)
        roles = self.channel_roles()
        for role, spins in self.spins.items():
            for spin in spins:
                spin.setEnabled(bool(roles[role]))
        for label, color in [("P", "#E7EAF0"), ("Tu", "#64B5F6"), ("Td", "#FFB363")]:
            key = self.channel_combos[label].currentData()
            if not key:
                continue
            channel = next(c for c in self.snapshot.channels if c.key == key)
            plot = self.pressure_plot if label == "P" else self.temperature_plot
            unit = self.controller.main.model.pressure_display_unit if label == "P" else "degC"
            values = display_values(
                self.snapshot.data[key].to_numpy(float), channel, unit
            )
            curve = plot.plot(
                times,
                values,
                pen=pg.mkPen(color, width=1.2),
                connect="finite",
                name=channel.source_column,
            )
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
        lo, hi = times[0], times[-1]
        for role in ROI_ROLES:
            if not roles[role]:
                continue
            plot = self.pressure_plot if role in ("Pi", "Pf") else self.temperature_plot
            initial = role in ("Pi", "Tu_i", "Td_i")
            color = QColor(70, 150, 240) if initial else QColor(75, 200, 130)
            region = pg.LinearRegionItem(
                self.bounds()[role],
                bounds=(lo, hi),
                swapMode="sort",
                brush=pg.mkBrush(color.red(), color.green(), color.blue(), 30),
                pen=pg.mkPen(color, width=1.4),
            )
            plot.addItem(region)
            region.sigRegionChanged.connect(
                lambda _region, r=role: self._region_changed(r)
            )
            self.regions[role] = region
        self.opening_lines = []
        for plot in (self.pressure_plot, self.temperature_plot):
            line = pg.InfiniteLine(
                self.opening.value(),
                movable=True,
                pen=pg.mkPen("#E6C45C", width=1.5, style=Qt.PenStyle.DashLine),
                bounds=(lo, hi),
            )
            line.setZValue(30)
            line.sigPositionChanged.connect(
                lambda moving: self.opening.setValue(float(moving.value()))
            )
            plot.addItem(line)
            self.opening_lines.append(line)
        self.pressure_plot.autoRange()
        self.temperature_plot.autoRange()
        self._syncing = False
        self._activate_region()
        self.controller.refresh_preview()

    def _activate_region(self, *_args):
        active = self.target.currentData()
        for role, region in self.regions.items():
            selected = role == active
            region.setMovable(selected)
            region.setZValue(25 if selected else 5)
            region.setAcceptedMouseButtons(
                Qt.MouseButton.LeftButton if selected else Qt.MouseButton.NoButton
            )
            for line in region.lines:
                line.setAcceptedMouseButtons(
                    Qt.MouseButton.LeftButton if selected else Qt.MouseButton.NoButton
                )
            region.setBrush(
                pg.mkBrush(70, 150, 240, 55 if selected else 20)
                if role in ("Pi", "Tu_i", "Td_i")
                else pg.mkBrush(75, 200, 130, 55 if selected else 20)
            )

    def _spin_changed(self, role):
        if self._syncing:
            return
        self.set_bounds(role, sorted(self.bounds()[role]))
        self.controller.refresh_preview()

    def _region_changed(self, role):
        if self._syncing:
            return
        self.set_bounds(role, self.regions[role].getRegion())
        self.controller.refresh_preview()

    def _opening_changed(self, *_args):
        if not self._syncing:
            self._syncing = True
            for line in getattr(self, "opening_lines", []):
                line.setValue(self.opening.value())
            self._syncing = False

    def _plot_clicked(self, event, plot):
        if (
            not self.snapshot
            or event.button() != Qt.MouseButton.LeftButton
            or event.isAccepted()
        ):
            return
        role = self.target.currentData()
        expected = self.pressure_plot if role in ("Pi", "Pf") else self.temperature_plot
        if plot is not expected or role not in self.regions:
            return
        vb = plot.plotItem.vb
        if not vb.sceneBoundingRect().contains(event.scenePos()):
            return
        center = vb.mapSceneToView(event.scenePos()).x()
        lo, hi = float(self.snapshot.data["Time"].iloc[0]), float(
            self.snapshot.data["Time"].iloc[-1]
        )
        width = min(self.click_width.value(), hi - lo)
        left = max(lo, min(center - width / 2, hi - width))
        self.set_bounds(role, (left, left + width))
        self.controller.refresh_preview()

    def match_temperature_windows(self):
        for role in ("Tu_i", "Td_i", "Tu_f", "Td_f"):
            self.set_bounds(role, self.bounds()["Pi" if role.endswith("_i") else "Pf"])
        self.controller.refresh_preview()

    def show_statistics(self, values, errors):
        for row, role in enumerate(ROI_ROLES):
            roi = values.get(role)
            if roi:roi=display_statistics(roi,self.controller.main.model.pressure_display_unit)
            texts = (
                [
                    f"{roi.mean:.7g}",
                    f"{roi.std:.4g}",
                    str(roi.count),
                    f"{roi.slope_per_min:.5g}",
                    roi.unit,
                ]
                if roi
                else ["—", "—", "—", "—", "—"]
            )
            for column, text in enumerate(texts, 3):
                cell = item(text)
                detail = (
                    f"{roi.source_column} [{roi.source_unit}] converted to {roi.unit}. "
                    f"Finite readings {roi.count}/{roi.total_count}; min {roi.minimum:.7g}; max {roi.maximum:.7g}; "
                    f"max gap {roi.max_time_gap_s:g} s. Source rows {roi.start_source_row}–{roi.end_source_row}. "
                    f"Included sample times {roi.start_s:g}–{roi.end_s:g} s."
                    if roi
                    else errors.get(role, "No channel assigned.")
                )
                cell.setToolTip(detail)
                self.roi_table.setItem(row, column, cell)
        self.preview_label.setText(
            " | ".join(errors.values())
            if errors
            else "Preview only. Blue = initial, green = final. Capture stores the exact samples and all channel assignments."
        )


class CalibrationFitWidget(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.eos = QComboBox()
        self.eos.addItems(EOS_OPTIONS)
        self.eos.currentTextChanged.connect(controller.set_eos)
        controls.addWidget(QLabel("Gas model:"))
        controls.addWidget(self.eos)
        calculate = QPushButton("Calculate volumes")
        calculate.clicked.connect(controller.calculate)
        controls.addWidget(calculate)
        for text, slot in [
            ("New session", controller.new_session),
            ("Save session…", controller.save),
            ("Open session…", controller.open_session),
            ("Export Excel…", controller.export),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        info = QLabel(
            "Include runs from both directions and known hollow volumes plus solid/side-only tests. "
            "Tu/Td are tank sensors; Tb is separate. Double-click a run to edit its ROIs and inputs."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.runs = QTableWidget(0, 12)
        self.runs.setHorizontalHeaderLabels(
            [
                "Include",
                "Run ID",
                "Side",
                "Plug",
                "Vp cm³",
                "Pi abs kPa",
                "Pf abs kPa",
                "Ti °C",
                "Tf °C",
                "Input status",
                "Exclusion reason",
                "Source file",
            ]
        )
        self.runs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runs.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.runs.horizontalHeader().setStretchLastSection(True)
        self.runs.cellChanged.connect(controller.run_cell_changed)
        self.runs.itemDoubleClicked.connect(lambda *_: controller.edit_selected())
        layout.addWidget(self.runs, 2)
        actions = QHBoxLayout()
        for text, slot in [
            ("Edit selected run", controller.edit_selected),
            ("Remove selected run", controller.remove_selected),
            ("Undo removal", controller.undo_remove),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        self.collection_label = QLabel("0 captured runs")
        actions.addWidget(self.collection_label)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = QLabel(
            "No fit calculated. Capture runs in the Check-plug ROI tab."
        )
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.status)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        self.volumes = QTableWidget(0, 4)
        self.volumes.setHorizontalHeaderLabels(
            ["Volume", "Estimate cm³", "OLS SE cm³", "Approx 95% ±"]
        )
        self.volumes.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        bottom.addWidget(self.volumes)
        self.residual_plot = pg.PlotWidget()
        self.residual_plot.showGrid(x=True, y=True, alpha=0.2)
        self.residual_plot.setLabel("bottom", "Included run number")
        self.residual_plot.setLabel("left", "Mass-balance residual", units="kPa cm³/K")
        self.residual_plot.getAxis("left").enableAutoSIPrefix(False)
        bottom.addWidget(self.residual_plot)
        layout.addWidget(bottom, 1)
        self.method = QLabel(
            "Unweighted OLS by SVD. Reported SE is conditional on the design matrix; it does not include full sensor/geometry uncertainty. "
            "The nitrogen model uses the manual’s approximation for the final nitrogen/air mixture."
        )
        self.method.setWordWrap(True)
        layout.addWidget(self.method)

    def invalidate(
        self, message="Inputs changed — calculate volumes to refresh results."
    ):
        self.current_fit=None
        self.volumes.setRowCount(0)
        self.residual_plot.clear()
        self.status.setText(message)

    def show_fit(self, fit):
        self.current_fit=fit
        unit=self.controller.main.model.pressure_display_unit
        factor=1000/PRESSURE_UNITS[unit]
        self.volumes.setRowCount(4)
        for row, key in enumerate(("VU", "VD", "DU", "DD")):
            se = fit.standard_errors_cm3[key]
            values = [
                key,
                f"{fit.volumes_cm3[key]:.8g}",
                f"{se:.5g}" if se is not None else "n/a",
                f"{se*1.96:.5g}" if se is not None else "n/a",
            ]
            for column, value in enumerate(values):
                self.volumes.setItem(row, column, item(value))
        self.residual_plot.clear()
        self.residual_plot.addItem(pg.InfiniteLine(0, angle=0, pen=pg.mkPen("#AAAAAA")))
        self.residual_plot.plot(
            list(range(1, len(fit.labels) + 1)),
            [r*factor for r in fit.residuals],
            symbol="o",
            pen=None,
            symbolBrush="#57A9DC",
            symbolSize=8,
        )
        r2 = f"{fit.r_squared:.8g}" if fit.r_squared is not None else "undefined"
        v = fit.volumes_cm3
        text = (
            f"{len(fit.labels)} included runs; rank {fit.rank}; condition {fit.condition_number:.4g}; "
            f"SSE {fit.sse*factor**2:.6g}; RMSE {fit.rmse*factor:.6g} {unit} cm³/K; R² {r2}. "
            f'VU+DU = {v["VU"]+v["DU"]:.6g} cm³; VD+DD = {v["VD"]+v["DD"]:.6g} cm³.'
        )
        if fit.warnings:
            text += "\n" + "\n".join(fit.warnings)
        self.status.setText(text)

    def set_pressure_unit(self,unit):
        self.runs.horizontalHeaderItem(5).setText(f'Pi abs {unit}')
        self.runs.horizontalHeaderItem(6).setText(f'Pf abs {unit}')
        self.residual_plot.setLabel('left','Mass-balance residual',units=f'{unit} cm³/K')
        if getattr(self,'current_fit',None) is not None:self.show_fit(self.current_fit)
