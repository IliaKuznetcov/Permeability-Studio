"""Qt-independent, unit-explicit extraction from manually chosen state windows."""

from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import numpy as np

from .core import display_values, interval_rows, timestamp_at
from .models import ApplicationModel
from .calibration_models import (
    CalibrationRun,
    LogSnapshot,
    ROIStatistics,
    ROI_ROLES,
    CONFIGURATIONS,
)


def snapshot_log(model: ApplicationModel) -> LogSnapshot:
    """Copy the current recording and identify its data and source file by hashes."""
    if model.data is None:
        raise ValueError("Open a recording first.")
    channels = [asdict(c) for c in model.channels]
    signature = json.dumps(
        {
            "path": str(model.source_path),
            "channels": channels,
            "date": model.date_column,
            "time": model.time_column,
        },
        sort_keys=True,
    )
    signature += model.data.to_json(orient="split", double_precision=15)
    dataset_id = sha256(signature.encode()).hexdigest()
    path = model.source_path
    source_hash = ""
    if path and path.is_file():
        source_hash = sha256(path.read_bytes()).hexdigest()
    from .models import SignalChannel

    return LogSnapshot(
        dataset_id=dataset_id,
        source_path=str(path or ""),
        source_sha256=source_hash,
        time_description=model.time_description,
        date_column=model.date_column,
        time_column=model.time_column,
        channels=[SignalChannel(**c) for c in channels],
        import_report=dict(model.import_report),
        data=model.data.copy(deep=True),
    )


def extract_roi(
    snapshot: LogSnapshot, channel_key: str, bounds, role: str
) -> ROIStatistics:
    """Compute statistics for one channel/window in kPa or Celsius.

    Missing readings remain gaps in the raw recording. Only finite readings
    contribute to the statistics, and at least three are required.
    """
    channel = None
    for candidate in snapshot.channels:
        if candidate.key == channel_key:
            channel = candidate
            break
    if channel is None:
        raise ValueError(f"{role}: choose a source channel.")
    expected = "Pressure" if role in ("Pi", "Pf") else "Temperature"
    if channel.quantity != expected:
        raise ValueError(f"{role}: choose a {expected.lower()} channel.")
    data = snapshot.data
    start, end = interval_rows(data, bounds, role)
    unit = "kPa" if expected == "Pressure" else "degC"
    values = display_values(
        data[channel_key].iloc[start : end + 1].to_numpy(float), channel, unit
    )
    times = data["Time"].iloc[start : end + 1].to_numpy(float)
    valid = np.isfinite(values)
    if valid.sum() < 3:
        raise ValueError(
            f"{role}: at least three valid {channel.source_column} readings are required."
        )
    values = values[valid]
    finite_times = times[valid]
    centered_times = finite_times - finite_times.mean()
    # Linear drift is covariance(time, value) / variance(time).
    # Times are in seconds; multiply by 60 to report the slope per minute.
    slope = float(
        np.dot(centered_times, values - values.mean())
        / np.dot(centered_times, centered_times)
        * 60
    )
    requested_start, requested_end = sorted(map(float, bounds))
    return ROIStatistics(
        role=role,
        channel_key=channel.key,
        source_column=channel.source_column,
        source_unit=channel.unit,
        unit=unit,
        requested_start_s=requested_start,
        requested_end_s=requested_end,
        start_s=float(times[0]),
        end_s=float(times[-1]),
        start_row=start,
        end_row=end,
        start_source_row=int(data["SourceRow"].iloc[start]),
        end_source_row=int(data["SourceRow"].iloc[end]),
        start_timestamp=timestamp_at(data, start),
        end_timestamp=timestamp_at(data, end),
        count=int(valid.sum()),
        total_count=len(times),
        mean=float(values.mean()),
        std=float(values.std(ddof=1)),
        minimum=float(values.min()),
        maximum=float(values.max()),
        slope_per_min=slope,
        max_time_gap_s=float(np.diff(finite_times).max()),
    )


def build_run(
    snapshot: LogSnapshot,
    *,
    bounds: dict,
    channel_roles: dict,
    label: str,
    side: str,
    plug_id: str,
    configuration: str,
    plug_volume_cm3: float,
    opening_time_s: float,
    pressure_basis: str = "Gauge",
    barometric_kpa: float | None = None,
    receiving_mode: str = "Vented",
    receiving_pressure_kpa: float | None = None,
    receiving_temperature_c: float | None = None,
    receiving_temperature_source: str = "",
    final_temperature_confirmed: bool = False,
    zi_override: float | None = None,
    zf_override: float | None = None,
    zb: float = 1.0,
    included: bool = True,
    exclusion_reason: str = "",
    notes: str = "",
    pressure_drift_limit_kpa_min: float | None = None,
    temperature_drift_limit_c_min: float | None = None,
    uid: str | None = None,
) -> CalibrationRun:
    """Validate a check-plug selection and store its statistics and conditions."""
    if not label.strip() or not plug_id.strip():
        raise ValueError("Enter a run ID and plug ID.")
    if side not in ("Upstream", "Downstream") or configuration not in CONFIGURATIONS:
        raise ValueError("Choose the expansion direction and plug configuration.")
    if pressure_basis not in ("Gauge", "Absolute") or receiving_mode not in (
        "Vented",
        "Measured absolute",
    ):
        raise ValueError("Choose pressure basis and receiving-pressure mode.")
    if not np.isfinite(plug_volume_cm3) or plug_volume_cm3 < 0:
        raise ValueError("Known plug volume must be finite and nonnegative.")
    if configuration == "Solid isolating" and plug_volume_cm3 != 0:
        raise ValueError("A solid isolating plug has zero accessible plug volume.")
    if not np.isfinite(opening_time_s):
        raise ValueError("Choose an expansion time.")
    rois = {}
    for role in ROI_ROLES:
        key = channel_roles.get(role)
        if key:
            if role not in bounds:
                raise ValueError(f"{role}: select an interval.")
            rois[role] = extract_roi(snapshot, key, bounds[role], role)
    required = (
        ("Pi", "Pf", "Tu_i", "Tu_f")
        if side == "Upstream"
        else ("Pi", "Pf", "Td_i", "Td_f")
    )
    for role in required:
        if role not in rois:
            raise ValueError(f"{role}: assign the required source channel.")
    for role, roi in rois.items():
        if role in ("Pi", "Tu_i", "Td_i") and roi.end_s >= opening_time_s:
            raise ValueError(f"{role}: initial ROI must end before the expansion time.")
        if role in ("Pf", "Tu_f", "Td_f") and roi.start_s <= opening_time_s:
            raise ValueError(f"{role}: final ROI must start after the expansion time.")
    for key, value in [
        ("barometric pressure", barometric_kpa),
        ("Zi", zi_override),
        ("Zf", zf_override),
        ("Zb", zb),
    ]:
        if value is not None and (not np.isfinite(value) or value <= 0):
            raise ValueError(f"{key} must be finite and positive when entered.")
    if receiving_pressure_kpa is not None and (
        not np.isfinite(receiving_pressure_kpa) or receiving_pressure_kpa < 0
    ):
        raise ValueError(
            "Receiving pressure must be a finite nonnegative absolute pressure."
        )
    if receiving_temperature_c is not None and (
        not np.isfinite(receiving_temperature_c) or receiving_temperature_c <= -273.15
    ):
        raise ValueError("Receiving temperature must be above absolute zero.")
    for limit in (pressure_drift_limit_kpa_min, temperature_drift_limit_c_min):
        if limit is not None and (not np.isfinite(limit) or limit <= 0):
            raise ValueError("Drift limits must be positive, or left off.")
    if not included and not exclusion_reason.strip():
        raise ValueError("Record a reason for excluding this run.")
    return CalibrationRun(
        uid=uid or str(uuid4()),
        label=label.strip(),
        dataset_id=snapshot.dataset_id,
        side=side,
        plug_id=plug_id.strip(),
        configuration=configuration,
        plug_volume_cm3=float(plug_volume_cm3),
        opening_time_s=float(opening_time_s),
        pressure_basis=pressure_basis,
        barometric_kpa=barometric_kpa,
        receiving_mode=receiving_mode,
        receiving_pressure_kpa=receiving_pressure_kpa,
        receiving_temperature_c=receiving_temperature_c,
        receiving_temperature_source=receiving_temperature_source.strip(),
        final_temperature_confirmed=bool(final_temperature_confirmed),
        zi_override=zi_override,
        zf_override=zf_override,
        zb=float(zb),
        rois=rois,
        included=bool(included),
        exclusion_reason=exclusion_reason.strip(),
        notes=notes.strip(),
        pressure_drift_limit_kpa_min=pressure_drift_limit_kpa_min,
        temperature_drift_limit_c_min=temperature_drift_limit_c_min,
    )


def run_warnings(run: CalibrationRun) -> list[str]:
    messages = []
    for role, roi in run.rois.items():
        if roi.count < roi.total_count:
            messages.append(f"{role}: {roi.total_count-roi.count} missing readings")
        limit = (
            run.pressure_drift_limit_kpa_min
            if role in ("Pi", "Pf")
            else run.temperature_drift_limit_c_min
        )
        if limit is not None and abs(roi.slope_per_min) > limit:
            messages.append(
                f"{role}: drift {roi.slope_per_min:.4g} {roi.unit}/min exceeds {limit:g}"
            )
    for state, prole in [("i", "Pi"), ("f", "Pf")]:
        pressure = run.rois[prole]
        for prefix in ("Tu_", "Td_"):
            temp = run.rois.get(prefix + state)
            if temp and (temp.start_s, temp.end_s) != (
                pressure.start_s,
                pressure.end_s,
            ):
                messages.append(
                    f"{prefix+state}: temperature and pressure windows differ"
                )
    if (
        run.pressure_drift_limit_kpa_min is None
        or run.temperature_drift_limit_c_min is None
    ):
        messages.append("Drift acceptance limits not fully configured; review slopes")
    return messages
