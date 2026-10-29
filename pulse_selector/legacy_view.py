"""Qt widgets and graph rendering. User actions are delegated to the controller."""

from __future__ import annotations
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHeaderView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QTabWidget,
)
from .models import PRESSURE_UNITS, TEMPERATURE_UNITS, PulseSelection, SignalChannel
from .core import guess_channel, display_values

NONE_LABEL = "<none>"


class ColumnMappingDialog(QDialog):
    """Choose time columns and a variable number of measurement channels."""

    def __init__(self, frame: pd.DataFrame, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map log columns")
        self.resize(850, 550)
        columns = [str(column) for column in frame.columns]
        self.date_combo = QComboBox()
        self.date_combo.addItem(NONE_LABEL)
        self.date_combo.addItems(columns)
        self.time_combo = QComboBox()
        self.time_combo.addItems(columns)
        self._guess_named_column(self.date_combo, columns, ("date", "day"), offset=1)
        self._guess_named_column(
            self.time_combo, columns, ("elapsed", "time", "seconds", "sec")
        )

        note = QLabel(
            "Choose one or more channels. Header-based suggestions can be changed. "
            "Verify quantity, units, and location. All channels must share the selected time column. "
            "Exports retain original units; the graph converts units within each quantity for display."
        )
        note.setWordWrap(True)
        self.channel_table = QTableWidget(len(columns), 5)
        self.channel_table.setHorizontalHeaderLabels(
            ["Use", "Source column", "Quantity", "Unit", "Location"]
        )
        self.channel_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        time_names = {self.date_combo.currentText(), self.time_combo.currentText()}
        for row, column in enumerate(columns):
            quantity, unit, location, recognized = guess_channel(column)
            numeric = pd.to_numeric(frame[column], errors="coerce")
            numeric_count = numeric.notna().sum()
            checkbox = QTableWidgetItem()
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            # Sparse summary columns beside a trace are not time-series defaults.
            # A genuinely sparse sensor can still be selected explicitly.
            checked = (
                recognized
                and numeric_count >= 3
                and numeric.notna().mean() > 0.8
                and "plateau" not in column.lower()
                and column not in time_names
            )
            checkbox.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.channel_table.setItem(row, 0, checkbox)
            item = QTableWidgetItem(column)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.channel_table.setItem(row, 1, item)
            quantity_combo = QComboBox()
            quantity_combo.addItems(["Pressure", "Temperature"])
            quantity_combo.setCurrentText(quantity)
            unit_combo = QComboBox()
            self._set_unit_choices(unit_combo, quantity)
            unit_combo.setCurrentText(unit)
            location_combo = QComboBox()
            location_combo.addItems(["Unspecified", "Upstream", "Downstream"])
            location_combo.setCurrentText(location)
            quantity_combo.currentTextChanged.connect(
                lambda value, target=unit_combo: self._set_unit_choices(target, value)
            )
            self.channel_table.setCellWidget(row, 2, quantity_combo)
            self.channel_table.setCellWidget(row, 3, unit_combo)
            self.channel_table.setCellWidget(row, 4, location_combo)
        self.channel_table.setColumnWidth(0, 45)
        self.date_combo.currentTextChanged.connect(self._sync_time_columns)
        self.time_combo.currentTextChanged.connect(self._sync_time_columns)
        self._sync_time_columns()
        form = QFormLayout(self)
        form.addRow(note)
        form.addRow("Date column (optional):", self.date_combo)
        form.addRow("Time or elapsed-time column:", self.time_combo)
        form.addRow(self.channel_table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _sync_time_columns(self, *_args) -> None:
        time_names = {self.date_combo.currentText(), self.time_combo.currentText()}
        for row in range(self.channel_table.rowCount()):
            is_time = self.channel_table.item(row, 1).text() in time_names
            checkbox = self.channel_table.item(row, 0)
            if is_time:
                checkbox.setCheckState(Qt.CheckState.Unchecked)
            flags = Qt.ItemFlag.ItemIsUserCheckable
            if not is_time:
                flags |= Qt.ItemFlag.ItemIsEnabled
            checkbox.setFlags(flags)
            for column in (2, 3, 4):
                self.channel_table.cellWidget(row, column).setEnabled(not is_time)

    @staticmethod
    def _set_unit_choices(combo: QComboBox, quantity: str) -> None:
        combo.clear()
        combo.addItems(
            list(PRESSURE_UNITS) if quantity == "Pressure" else list(TEMPERATURE_UNITS)
        )

    @staticmethod
    def _guess_named_column(combo, columns, keywords, offset=0) -> None:
        for keyword in keywords:
            for index, column in enumerate(columns):
                if keyword in column.lower():
                    combo.setCurrentIndex(index + offset)
                    return

    def mapping(self) -> tuple[str | None, str, list[SignalChannel]]:
        date = self.date_combo.currentText()
        date = None if date == NONE_LABEL else date
        time = self.time_combo.currentText()
        channels = []
        for row in range(self.channel_table.rowCount()):
            if self.channel_table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            column = self.channel_table.item(row, 1).text()
            if column in {date, time}:
                raise ValueError(
                    "Date/time columns cannot also be measurement channels."
                )
            channels.append(
                SignalChannel(
                    f"Signal_{len(channels)+1}",
                    column,
                    self.channel_table.cellWidget(row, 2).currentText(),
                    self.channel_table.cellWidget(row, 3).currentText(),
                    self.channel_table.cellWidget(row, 4).currentText(),
                )
            )
        if date is not None and date == time:
            raise ValueError(
                "Choose different date and time columns, or use no separate date column."
            )
        if not channels:
            raise ValueError("Select at least one measurement channel.")
        return date, time, channels

    def accept(self) -> None:
        try:
            self.mapping()
        except ValueError as exc:
            QMessageBox.warning(self, "Column mapping", str(exc))
            return
        super().accept()


class PulseSelectorWindow(QMainWindow):
    def __init__(self, controller) -> None:
        super().__init__()
        self.setWindowTitle("Pulse picker and check-plug calibration — 3.0")
        self.resize(1500, 880)

        self.controller = controller
        self.model = controller.model
        self.curves = []
        self.selection_overlays: list[
            tuple[pg.LinearRegionItem, pg.LinearRegionItem, pg.TextItem]
        ] = []

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Elapsed time", units="s")
        self.plot.plotItem.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.setLabel("left", "Pressure", units="psi")
        self.legend = self.plot.addLegend()
        self.plot.scene().sigMouseClicked.connect(self._plot_clicked)
        # A second ViewBox shares time with the main plot, with its own Y scale.
        self.temperature_view = pg.ViewBox(enableMenu=False)
        self.temperature_view.setMouseEnabled(x=False, y=False)
        self.temperature_view.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.plot.scene().addItem(self.temperature_view)
        self.plot.plotItem.getAxis("right").linkToView(self.temperature_view)
        self.temperature_view.setXLink(self.plot.plotItem.vb)
        self.plot.plotItem.vb.sigResized.connect(self._update_secondary_view)
        self.plot.plotItem.hideAxis("right")

        self.region = self._new_active_region(0.0, 1.0)
        self.region.hide()
        self.plot.addItem(self.region)

        self.plateau_region = self._new_plateau_region(0.0, 1.0)
        self.plateau_region.hide()
        self.plot.addItem(self.plateau_region)

        self.capture_button = QPushButton("Capture pulse + plateau")
        self.capture_button.clicked.connect(self.controller.capture_selection)
        self.capture_button.setEnabled(False)

        self.undo_button = QPushButton("Undo last")
        self.undo_button.clicked.connect(self.controller.undo_last)
        self.undo_button.setEnabled(False)

        self.clear_button = QPushButton("Clear all")
        self.clear_button.clicked.connect(self.controller.clear_selections)
        self.clear_button.setEnabled(False)

        self.export_button = QPushButton("Export selections")
        self.export_button.clicked.connect(self.controller.export_selections)
        self.export_button.setEnabled(False)

        self.default_width_spin = QSpinBox()
        self.default_width_spin.setRange(1, 1_000_000)
        self.default_width_spin.setValue(3600)
        self.default_width_spin.setSuffix(" s")

        self.pre_context_spin = QSpinBox()
        self.pre_context_spin.setRange(0, 1_000_000)
        self.pre_context_spin.setValue(120)
        self.pre_context_spin.setSuffix(" s")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Default window width:"))
        controls.addWidget(self.default_width_spin)
        controls.addSpacing(20)
        controls.addWidget(QLabel("Pre-opening context retained:"))
        controls.addWidget(self.pre_context_spin)
        controls.addStretch(1)
        controls.addWidget(self.capture_button)
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addWidget(self.export_button)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Pulse",
                "Start elapsed",
                "End elapsed",
                "Duration",
                "Start timestamp",
                "End timestamp",
                "Start row",
                "End row",
                "Context start",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemSelectionChanged.connect(self._refresh_saved_plateau)
        self.table.itemDoubleClicked.connect(self._edit_saved_plateau)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumWidth(390)

        self.click_target = QComboBox()
        self.click_target.addItems(["Pulse (blue)", "Plateau (orange)"])
        self.click_target.setToolTip(
            "Choose which interval a click on the plot positions. Both windows can be dragged."
        )
        self.plateau_width_spin = QSpinBox()
        self.plateau_width_spin.setRange(1, 1_000_000)
        self.plateau_width_spin.setValue(60)
        self.plateau_width_spin.setSuffix(" s")
        self.plateau_width_spin.setToolTip(
            "Width used by 'Place at pulse end'. Drag the orange edges for any other interval."
        )
        self.reset_plateau_button = QPushButton("Place at pulse end")
        self.reset_plateau_button.clicked.connect(self.reset_plateau_to_tail)
        self.update_plateau_button = QPushButton("Save plateau to selected pulse")
        self.update_plateau_button.clicked.connect(
            self.controller.update_selected_plateau
        )
        self.update_plateau_button.setEnabled(False)
        plateau_controls = QHBoxLayout()
        plateau_controls.addWidget(QLabel("Click moves:"))
        plateau_controls.addWidget(self.click_target)
        plateau_controls.addWidget(QLabel("Plateau width:"))
        plateau_controls.addWidget(self.plateau_width_spin)
        plateau_controls.addWidget(self.reset_plateau_button)
        plateau_controls.addStretch(1)
        self.plateau_preview = QLabel(
            "Orange = plateau interval. Adjust it to a stable portion before capture."
        )
        self.plateau_preview.setWordWrap(True)
        self.plateau_region.sigRegionChanged.connect(self._refresh_plateau_preview)

        plot_container = QWidget()
        plot_layout = QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.plot)
        self.channel_info = QLabel(
            "Choose pressure and/or temperature channels when opening a log."
        )
        self.channel_info.setWordWrap(True)
        plot_layout.addWidget(self.channel_info)
        plot_layout.addLayout(controls)
        plot_layout.addLayout(plateau_controls)
        plot_layout.addWidget(self.plateau_preview)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(plot_container)
        selection_panel = QWidget()
        selection_layout = QVBoxLayout(selection_panel)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.addWidget(
            QLabel("Captured pulses (scroll for interval metadata)")
        )
        selection_layout.addWidget(self.table, 3)
        self.saved_plateau_label = QLabel("Selected pulse plateau: no pulse selected")
        selection_layout.addWidget(self.saved_plateau_label)
        self.saved_plateau_table = QTableWidget(0, 5)
        self.saved_plateau_table.setHorizontalHeaderLabels(
            ["Channel", "Mean", "Sample SD", "n", "Unit"]
        )
        self.saved_plateau_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.saved_plateau_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 5):
            self.saved_plateau_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        selection_layout.addWidget(self.saved_plateau_table, 1)
        edit_controls = QHBoxLayout()
        self.edit_plateau_button = QPushButton("Edit selected plateau")
        self.edit_plateau_button.setEnabled(False)
        self.edit_plateau_button.clicked.connect(self._edit_saved_plateau)
        edit_controls.addWidget(self.edit_plateau_button)
        edit_controls.addWidget(self.update_plateau_button)
        selection_layout.addLayout(edit_controls)
        splitter.addWidget(selection_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Pulse picker")
        self.setCentralWidget(self.tabs)
        splitter.setSizes([1000, 500])

        toolbar = QToolBar("File")
        self.addToolBar(toolbar)

        open_action = QAction("Open log", self)
        open_action.triggered.connect(self.controller.open_log)
        toolbar.addAction(open_action)

        export_action = QAction("Export active tab", self)
        export_action.triggered.connect(self.controller.export_active_tab)
        toolbar.addAction(export_action)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Open a log, position the blue pulse and orange plateau intervals, then capture both."
        )

    @staticmethod
    def _new_active_region(
        left: float,
        right: float,
        bounds: tuple[float, float] | None = None,
    ) -> pg.LinearRegionItem:
        region = pg.LinearRegionItem(
            values=(left, right),
            orientation="vertical",
            movable=True,
            bounds=bounds,
            swapMode="sort",
            brush=pg.mkBrush(QColor(80, 150, 255, 55)),
            pen=pg.mkPen(QColor(80, 150, 255), width=2),
        )
        region.setZValue(20)
        return region

    def _update_secondary_view(self) -> None:
        self.temperature_view.setGeometry(self.plot.plotItem.vb.sceneBoundingRect())
        self.temperature_view.linkedViewChanged(
            self.plot.plotItem.vb, self.temperature_view.XAxis
        )

    def _plot_channels(self) -> None:
        self.plot.clear()
        self.temperature_view.clear()
        self.legend.clear()
        self.curves = []
        pressures = [c for c in self.model.channels if c.quantity == "Pressure"]
        temperatures = [c for c in self.model.channels if c.quantity == "Temperature"]
        primary = pressures or temperatures
        secondary = temperatures if pressures and temperatures else []
        primary_unit = primary[0].unit
        secondary_unit = secondary[0].unit if secondary else None
        self.plot.setLabel("left", primary[0].quantity, units=primary_unit)
        self.plot.plotItem.getAxis("left").enableAutoSIPrefix(False)
        if secondary:
            self.plot.plotItem.showAxis("right")
            self.plot.setLabel("right", "Temperature", units=secondary_unit)
            self.plot.plotItem.getAxis("right").enableAutoSIPrefix(False)
        else:
            self.plot.plotItem.hideAxis("right")
        times = self.model.data["Time"].to_numpy(float)
        for index, channel in enumerate(self.model.channels):
            on_right = bool(secondary) and channel.quantity == "Temperature"
            unit = secondary_unit if on_right else primary_unit
            values = display_values(
                self.model.data[channel.key].to_numpy(float), channel, unit
            )
            color = pg.intColor(index, hues=max(8, len(self.model.channels)))
            curve = pg.PlotDataItem(
                times, values, pen=pg.mkPen(color, width=1.2), connect="finite"
            )
            if on_right:
                self.temperature_view.addItem(curve)
            else:
                self.plot.addItem(curve)
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            # Explicit legend registration also includes curves on the right axis.
            self.legend.addItem(curve, f"{channel.source_column} [{unit}]")
            self.curves.append(curve)
        time_min, time_max = float(times[0]), float(times[-1])
        initial_right = min(time_min + self.default_width_spin.value(), time_max)
        self.region = self._new_active_region(
            time_min, initial_right, bounds=(time_min, time_max)
        )
        self.region.hide()
        self.plot.addItem(self.region)
        self.plateau_region = self._new_plateau_region(
            time_min, initial_right, bounds=(time_min, time_max)
        )
        self.plateau_region.hide()
        self.plateau_region.sigRegionChanged.connect(self._refresh_plateau_preview)
        self.plot.addItem(self.plateau_region)
        self.plot.autoRange()
        self._update_secondary_view()
        if secondary:
            self.temperature_view.enableAutoRange(axis=pg.ViewBox.YAxis)
        missing = sum(self.model.import_report["missing_samples"].values())
        self.channel_info.setText(
            f"{len(self.model.channels)} selected channels. Left: {primary[0].quantity} ({primary_unit}). "
            + (f"Right: Temperature ({secondary_unit}). " if secondary else "")
            + f"Original values and units are preserved in exports. Missing readings: {missing}."
        )

    def _plot_clicked(self, event) -> None:
        if self.model.data is None or event.button() != Qt.MouseButton.LeftButton:
            return
        # Region dragging owns its mouse event; do not reposition the other interval.
        if event.isAccepted():
            return
        if not self.plot.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
        self.position_interval(
            float(point.x()), plateau=self.click_target.currentIndex() == 1
        )

    def position_interval(self, center: float, plateau: bool = False) -> None:
        """Position the chosen interval, clamping its width to the recording."""
        if self.model.data is None:
            return
        target = self.plateau_region if plateau else self.region
        width = float(
            self.plateau_width_spin.value()
            if plateau
            else self.default_width_spin.value()
        )
        if target.isVisible():
            left, right = target.getRegion()
            width = max(float(right - left), 1e-9)
        time_min = float(self.model.data["Time"].iloc[0])
        time_max = float(self.model.data["Time"].iloc[-1])
        width = min(width, time_max - time_min)
        left = max(time_min, min(center - width / 2, time_max - width))
        target.setRegion((left, left + width))
        target.show()
        if not plateau:
            self.reset_plateau_to_tail()
        self._refresh_plateau_preview()
        self.statusBar().showMessage(
            "Blue = pulse; orange = plateau. Drag either interval or its edges, then capture both."
        )

    @staticmethod
    def _new_plateau_region(left, right, bounds=None):
        region = pg.LinearRegionItem(
            values=(left, right),
            bounds=bounds,
            movable=True,
            swapMode="sort",
            brush=pg.mkBrush(QColor(245, 165, 40, 65)),
            pen=pg.mkPen(QColor(225, 135, 20), width=2),
        )
        region.setZValue(21)
        return region

    def reset_plateau_to_tail(self) -> None:
        if self.model.data is None or not self.region.isVisible():
            return
        left, right = self.region.getRegion()
        self.plateau_region.setRegion(
            (max(left, right - self.plateau_width_spin.value()), right)
        )
        self.plateau_region.show()
        self._refresh_plateau_preview()

    def _refresh_plateau_preview(self) -> None:
        if self.model.data is None or not self.plateau_region.isVisible():
            return
        try:
            plateau = self.controller.preview_plateau(self.plateau_region.getRegion())
        except ValueError as exc:
            self.plateau_preview.setText(str(exc))
            return
        parts = []
        for channel in self.model.channels:
            value = plateau.values[channel.key]
            mean = f"{value.mean:.6g}" if value.mean is not None else "missing"
            sd = f"{value.std:.3g}" if value.std is not None else "n/a"
            parts.append(
                f"{channel.source_column}: {mean} {channel.unit} (SD {sd}, n={value.count})"
            )
        self.plateau_preview.setText(
            f"Plateau {plateau.start_time_s:g}–{plateau.end_time_s:g} s | "
            + " | ".join(parts)
        )

    def _edit_saved_plateau(self, *_args) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.model.selections):
            return
        plateau = self.model.selections[row].plateau
        self.plateau_region.setRegion((plateau.start_time_s, plateau.end_time_s))
        self.plateau_region.show()
        self.click_target.setCurrentIndex(1)
        self._refresh_plateau_preview()
        self.statusBar().showMessage(
            f"Editing plateau for pulse {row+1}. Adjust orange interval, then click Save plateau to selected pulse."
        )

    def _add_overlay(self, selection: PulseSelection) -> None:
        overlay = pg.LinearRegionItem(
            values=(selection.start_time_s, selection.end_time_s),
            orientation="vertical",
            movable=False,
            brush=pg.mkBrush(QColor(80, 170, 100, 55)),
            pen=pg.mkPen(QColor(40, 120, 60), width=1),
        )
        overlay.setZValue(5)
        self.plot.addItem(overlay)

        label = pg.TextItem(
            text=f"Pulse {selection.number}",
            anchor=(0.5, 1.0),
        )
        label.setZValue(25)
        midpoint = 0.5 * (selection.start_time_s + selection.end_time_s)
        y_min, y_max = self.plot.plotItem.vb.viewRange()[1]
        label.setPos(midpoint, y_max - 0.05 * (y_max - y_min))
        self.plot.addItem(label)
        plateau_overlay = pg.LinearRegionItem(
            values=(selection.plateau.start_time_s, selection.plateau.end_time_s),
            movable=False,
            brush=pg.mkBrush(QColor(245, 165, 40, 28)),
            pen=pg.mkPen(QColor(190, 120, 20), width=1),
        )
        plateau_overlay.setZValue(6)
        self.plot.addItem(plateau_overlay)
        self.selection_overlays.append((overlay, plateau_overlay, label))

    def _refresh_table(self) -> None:
        headers = [
            "Pulse",
            "Start elapsed (s)",
            "End elapsed (s)",
            "Duration (s)",
            "Start timestamp",
            "End timestamp",
            "Start row",
            "End row",
            "Context start",
            "Plateau start (s)",
            "Plateau end (s)",
        ]
        headers += [
            f"Plateau {c.source_column} [{c.unit}]" for c in self.model.channels
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.model.selections))
        for row, selection in enumerate(self.model.selections):
            values = [
                selection.number,
                f"{selection.start_time_s:.6g}",
                f"{selection.end_time_s:.6g}",
                f"{selection.end_time_s - selection.start_time_s:.6g}",
                selection.start_timestamp,
                selection.end_timestamp,
                selection.start_row,
                selection.end_row,
                selection.context_start_timestamp
                or f"{selection.context_start_time_s:.6g}",
                f"{selection.plateau.start_time_s:.6g}",
                f"{selection.plateau.end_time_s:.6g}",
            ]
            for channel in self.model.channels:
                mean = selection.plateau.values[channel.key].mean
                values.append(f"{mean:.6g}" if mean is not None else "missing")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column not in (4, 5, 8):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column >= 11:
                    stat = selection.plateau.values[
                        self.model.channels[column - 11].key
                    ]
                    sd = f"{stat.std:.6g}" if stat.std is not None else "n/a"
                    item.setToolTip(
                        f"Arithmetic mean; sample SD = {sd}; valid n = {stat.count}. Original units."
                    )
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self._refresh_saved_plateau()

    def _update_buttons(self) -> None:
        has_selections = bool(self.model.selections)
        self.undo_button.setEnabled(has_selections)
        self.clear_button.setEnabled(has_selections)
        self.export_button.setEnabled(has_selections)
        can_edit = has_selections and self.table.currentRow() >= 0
        self.update_plateau_button.setEnabled(can_edit)
        self.edit_plateau_button.setEnabled(can_edit)

    def _refresh_saved_plateau(self) -> None:
        if not hasattr(self, "saved_plateau_table"):
            return
        row = self.table.currentRow()
        if not 0 <= row < len(self.model.selections):
            self.saved_plateau_label.setText(
                "Selected pulse plateau: no pulse selected"
            )
            self.saved_plateau_table.setRowCount(0)
            return
        selection = self.model.selections[row]
        plateau = selection.plateau
        self.saved_plateau_label.setText(
            f"Pulse {selection.number} plateau: {plateau.start_time_s:g}–{plateau.end_time_s:g} s (original units)"
        )
        self.saved_plateau_table.setRowCount(len(self.model.channels))
        for row, channel in enumerate(self.model.channels):
            stat = plateau.values[channel.key]
            values = [
                channel.source_column,
                f"{stat.mean:.6g}" if stat.mean is not None else "missing",
                f"{stat.std:.6g}" if stat.std is not None else "n/a",
                stat.count,
                channel.unit,
            ]
            for column, value in enumerate(values):
                self.saved_plateau_table.setItem(
                    row, column, QTableWidgetItem(str(value))
                )

    def closeEvent(self, event):
        calibration = getattr(self.controller, "calibration", None)
        if calibration is not None and not calibration.maybe_save():
            event.ignore()
            return
        super().closeEvent(event)
