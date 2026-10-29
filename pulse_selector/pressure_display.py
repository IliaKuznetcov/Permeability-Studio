"""Pressure display conversion; stored statistics and solver inputs stay canonical."""
from dataclasses import replace
from .models import PRESSURE_UNITS


def display_statistics(stat,unit):
    if stat.unit!='kPa':return stat
    factor=1000/PRESSURE_UNITS[unit]
    return replace(stat,unit=unit,**{k:getattr(stat,k)*factor for k in
        ('mean','std','minimum','maximum','slope_per_min')})


def pressure_text(value_pa,unit,*,absolute=False):
    label=('psia' if unit=='psi' else 'kPa abs') if absolute else unit
    return f'{value_pa/PRESSURE_UNITS[unit]:.7g} {label}'
