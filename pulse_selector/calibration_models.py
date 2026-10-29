"""Data objects for manual state extraction and a multi-recording calibration."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from .models import SignalChannel

ROI_ROLES = ("Pi", "Pf", "Tu_i", "Tu_f", "Td_i", "Td_f")
CONFIGURATIONS = ("Through-bore", "Solid isolating", "Side-only cavity")


@dataclass(frozen=True)
class ROIStatistics:
    """One channel's state-window statistics plus units and source references."""

    role: str
    channel_key: str
    source_column: str
    source_unit: str
    unit: str
    requested_start_s: float
    requested_end_s: float
    start_s: float
    end_s: float
    start_row: int
    end_row: int
    start_source_row: int
    end_source_row: int
    start_timestamp: str
    end_timestamp: str
    count: int
    total_count: int
    mean: float
    std: float
    minimum: float
    maximum: float
    slope_per_min: float
    max_time_gap_s: float

    @property
    def coverage(self):
        return self.count / self.total_count


@dataclass
class LogSnapshot:
    """A retained recording: raw-unit samples, channel mapping and provenance."""

    dataset_id: str
    source_path: str
    source_sha256: str
    time_description: str
    date_column: str | None
    time_column: str
    channels: list[SignalChannel]
    import_report: dict
    data: pd.DataFrame


@dataclass
class CalibrationRun:
    """One check-plug expansion, including selected ROIs and gas-law conditions."""

    uid: str
    label: str
    dataset_id: str
    side: str
    plug_id: str
    configuration: str
    plug_volume_cm3: float
    opening_time_s: float
    pressure_basis: str
    barometric_kpa: float | None
    receiving_mode: str
    receiving_pressure_kpa: float | None
    receiving_temperature_c: float | None
    receiving_temperature_source: str
    final_temperature_confirmed: bool
    zi_override: float | None
    zf_override: float | None
    zb: float
    rois: dict[str, ROIStatistics]
    included: bool = True
    exclusion_reason: str = ""
    notes: str = ""
    pressure_drift_limit_kpa_min: float | None = None
    temperature_drift_limit_c_min: float | None = None


@dataclass
class CalibrationProject:
    """The calibration controller's recordings, captured runs and EOS choice."""

    snapshots: dict[str, LogSnapshot] = field(default_factory=dict)
    runs: list[CalibrationRun] = field(default_factory=list)
    eos: str = "Peng–Robinson nitrogen"
