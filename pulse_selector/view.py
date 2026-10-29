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
    """Top-level composition only; each tab owns its own view/controller."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        from . import __version__
        from .branding import APP_NAME, application_icon
        self.setWindowTitle(f"{APP_NAME} — {__version__}")
        self.setWindowIcon(application_icon())
        self.resize(1550, 940)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        toolbar = QToolBar("File")
        self.addToolBar(toolbar)
        action = QAction("Open log", self)
        action.triggered.connect(controller.open_log)
        toolbar.addAction(action)
        export = QAction("Export active tab", self)
        export.triggered.connect(controller.export_active_tab)
        toolbar.addAction(export)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel('Pressure display: '))
        self.pressure_units=QComboBox()
        self.pressure_units.addItems(['kPa','psi'])
        self.pressure_units.setCurrentText(controller.model.pressure_display_unit)
        toolbar.addWidget(self.pressure_units)
        self.pressure_units.currentTextChanged.connect(controller.set_pressure_unit)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Open a log, select initial/final pulse regions and mark valve opening."
        )

    def closeEvent(self, event):
        for name in ("pulses", "calibration"):
            controller = getattr(self.controller, name, None)
            if controller is not None and not controller.maybe_save():
                event.ignore()
                return
        super().closeEvent(event)
