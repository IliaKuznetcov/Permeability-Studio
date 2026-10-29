"""Data for paired-state permeability pulses, independent of Qt."""

from __future__ import annotations
from dataclasses import dataclass, field
from .calibration_models import LogSnapshot, ROIStatistics


@dataclass
class PairedPulse:
    """One captured pulse; raw samples are in the snapshot named by dataset_id."""

    uid: str
    label: str
    dataset_id: str
    valve_time_s: float
    initial_pressure_bounds: tuple[float, float]
    final_pressure_bounds: tuple[float, float]
    initial_temperature_bounds: tuple[float, float]
    final_temperature_bounds: tuple[float, float]
    initial: dict[str, ROIStatistics]
    final: dict[str, ROIStatistics]
    decay_start_row: int
    decay_end_row: int
    context_start_row: int
    fit_start_s: float
    fit_end_s: float
    fit_start_row: int
    fit_end_row: int
    pre_context_s: float
    sample_id: str = ""
    notes: str = ""


@dataclass
class PulseProject:
    """Recordings, captured pulses and shared settings for one pulse session."""

    snapshots: dict[str, LogSnapshot] = field(default_factory=dict)
    pulses: list[PairedPulse] = field(default_factory=list)
    pressure_basis: str = "Gauge"
    barometric_kpa: float | None = None
    calibrated_volumes_cm3: dict[str, float] = field(default_factory=dict)
    calibration_provenance: dict = field(default_factory=dict)
    analysis_settings: dict = field(default_factory=dict)
    analysis_results: dict = field(default_factory=dict)
    sample_results: dict = field(default_factory=dict)
