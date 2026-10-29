"""Plain data objects; no Qt or file dialogs."""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd

PRESSURE_UNITS = {
    "psi": 6894.757293168,
    "kPa": 1000.0,
    "MPa": 1e6,
    "Pa": 1.0,
    "bar": 1e5,
}
TEMPERATURE_UNITS = ("degC", "K", "degF")


@dataclass
class SignalChannel:
    key: str
    source_column: str
    quantity: str
    unit: str
    location: str = "Unspecified"


@dataclass(frozen=True)
class PlateauValue:
    """Statistics in the channel's original unit; missing values are excluded."""

    mean: float | None
    std: float | None
    count: int


@dataclass(frozen=True)
class PlateauSelection:
    start_time_s: float
    end_time_s: float
    start_row: int
    end_row: int
    start_timestamp: str
    end_timestamp: str
    values: dict[str, PlateauValue]


@dataclass
class PulseSelection:
    plateau: PlateauSelection
    number: int
    start_time_s: float
    end_time_s: float
    start_row: int
    end_row: int
    context_start_time_s: float
    context_start_row: int
    start_timestamp: str
    end_timestamp: str
    context_start_timestamp: str


@dataclass
class ApplicationModel:
    data: pd.DataFrame | None = None
    source_path: Path | None = None
    date_column: str | None = None
    time_column: str = ""
    channels: list[SignalChannel] = field(default_factory=list)
    import_report: dict = field(default_factory=dict)
    time_description: str = ""
    selections: list[PulseSelection] = field(default_factory=list)
    pressure_display_unit: str = 'kPa'
