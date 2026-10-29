"""A pressure input with an exact canonical kPa value behind display rounding."""
from PySide6.QtWidgets import QDoubleSpinBox
from .models import PRESSURE_UNITS


class PressureSpinBox(QDoubleSpinBox):
    def __init__(self,low,high,value=0.,decimals=6,special=None,qualifier='abs'):
        super().__init__()
        self.unit='kPa'
        self.qualifier=qualifier
        self.canonical_range=(low,high)
        self._canonical=value
        self._setting=False
        self.setDecimals(decimals)
        self.setKeyboardTracking(False)
        if special:self.setSpecialValueText(special)
        self.setRange(low,high)
        self.setValue(value)
        self.valueChanged.connect(self._remember)
        self.set_pressure_unit('kPa')

    def _remember(self,value):
        if not self._setting:self._canonical=value*PRESSURE_UNITS[self.unit]/1000

    def canonical_value(self):
        return self._canonical

    def set_canonical_value(self,value):
        self._canonical=float(value)
        self._setting=True
        try:self.setValue(value*1000/PRESSURE_UNITS[self.unit])
        finally:self._setting=False

    def set_pressure_unit(self,unit):
        blocked=self.blockSignals(True)
        self.unit=unit
        factor=1000/PRESSURE_UNITS[unit]
        self.setRange(*(v*factor for v in self.canonical_range))
        self.setValue(self._canonical*factor)
        suffix=f' {unit}/min' if self.qualifier=='/min' else f' {unit} {self.qualifier}'
        self.setSuffix(suffix)
        self.blockSignals(blocked)
