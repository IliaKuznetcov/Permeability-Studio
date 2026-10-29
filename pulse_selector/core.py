"""Import, time processing, unit conversion and pulse/plateau calculations.

This module has no UI dependency and can be used directly from scripts.
"""

from __future__ import annotations
from pathlib import Path
import re
import numpy as np
import pandas as pd
from .models import (
    PRESSURE_UNITS,
    TEMPERATURE_UNITS,
    SignalChannel,
    PlateauValue,
    PlateauSelection,
    PulseSelection,
)


def guess_channel(name: str) -> tuple[str, str, str, bool]:
    """Suggest metadata from a header, never from a channel's amplitude."""
    text = name.lower().strip()
    temperature = bool(re.search(r"temperature|temp|deg[cf]|°[cf]|^t[ud]\b", text))
    pressure = bool(re.search(r"pressure|^p(?:[ud]|[0-9]+)?\b|p_inlet|p_outlet", text))
    quantity = "Temperature" if temperature else "Pressure"
    if temperature:
        if re.search(r"degf|°f", text):
            unit = "degF"
        elif re.search(r"\bk\b", text):
            unit = "K"
        else:
            unit = "degC"
    else:
        unit = next(
            (
                u
                for u in PRESSURE_UNITS
                if re.search(r"(?<![a-z])" + u.lower() + r"(?![a-z])", text)
            ),
            "psi",
        )
    location = "Unspecified"
    if re.search(r"upstream|inlet|^p[u]\b|^t[u]\b", text):
        location = "Upstream"
    elif re.search(r"downstream|outlet|^p[d]\b|^t[d]\b", text):
        location = "Downstream"
    return quantity, unit, location, temperature or pressure


def display_values(
    values: np.ndarray, channel: SignalChannel, target_unit: str
) -> np.ndarray:
    """Convert a plotting copy. Source samples and exports remain untouched."""
    values = np.asarray(values, dtype=float)
    if channel.quantity == "Pressure":
        return values * PRESSURE_UNITS[channel.unit] / PRESSURE_UNITS[target_unit]
    # Convert to Celsius first, then to the requested display unit.
    if channel.unit == "K":
        celsius = values - 273.15
    elif channel.unit == "degF":
        celsius = (values - 32) * 5 / 9
    else:
        celsius = values

    if target_unit == "K":
        return celsius + 273.15
    if target_unit == "degF":
        return celsius * 9 / 5 + 32
    return celsius.copy()


def prepare_signals(frame, date_column, time_column, channels):
    """Use one shared clock; preserve missing readings independently per channel."""
    frame = frame.reset_index(drop=True)
    if not channels:
        raise ValueError("Select at least one measurement channel.")
    selected = [c.source_column for c in channels]
    if len(set(selected)) != len(selected) or len({c.key for c in channels}) != len(
        channels
    ):
        raise ValueError("Measurement channels must be unique.")
    if any(name in {date_column, time_column} for name in selected):
        raise ValueError("Date/time columns cannot also be measurement channels.")
    elapsed, timestamps, description, repairs = build_elapsed_time(
        frame, date_column, time_column
    )
    clean = pd.DataFrame(
        {"Time": elapsed, "Timestamp": timestamps, "SourceRow": np.arange(len(frame))}
    )
    for channel in channels:
        units = PRESSURE_UNITS if channel.quantity == "Pressure" else TEMPERATURE_UNITS
        if (
            channel.quantity not in {"Pressure", "Temperature"}
            or channel.unit not in units
        ):
            raise ValueError(f"Unsupported quantity/unit for {channel.source_column}.")
        clean[channel.key] = pd.to_numeric(
            frame[channel.source_column], errors="coerce"
        ).replace([np.inf, -np.inf], np.nan)
    invalid_time = int((~np.isfinite(clean["Time"])).sum())
    clean = clean.loc[np.isfinite(clean["Time"])].reset_index(drop=True)
    keep = np.ones(len(clean), dtype=bool)
    last_time = -np.inf
    for row, value in enumerate(clean["Time"].to_numpy(float)):
        if value <= last_time:
            keep[row] = False
        else:
            last_time = value
    removed = int((~keep).sum())
    clean = clean.loc[keep].reset_index(drop=True)
    if len(clean) < 3:
        raise ValueError(
            "Fewer than three rows remain with usable increasing timestamps."
        )
    missing = {}
    for channel in channels:
        valid = int(clean[channel.key].notna().sum())
        if valid < 3:
            raise ValueError(
                f"{channel.source_column}: fewer than three usable numeric readings."
            )
        missing[channel.key] = int(len(clean) - valid)
    return (
        clean,
        description,
        {
            "repaired_date_rows": repairs,
            "invalid_time_rows": invalid_time,
            "nonincreasing_rows": removed,
            "missing_samples": missing,
        },
    )


def _repair_isolated_date_spikes(seconds: np.ndarray) -> tuple[np.ndarray, int]:
    """Repair isolated one-row ±1-day date errors seen at midnight.

    Some logger exports assign 23:59:59 to the following date and then return
    to 00:00:00 of that same date. This produces a +86401 s jump followed by
    a -86399 s jump. Only that isolated pattern is repaired.
    """
    repaired = np.asarray(seconds, dtype=float).copy()
    if repaired.size < 3:
        return repaired, 0

    differences = np.diff(repaired)
    short_positive = differences[(differences > 0.0) & (differences <= 60.0)]
    ordinary_step = float(np.median(short_positive)) if short_positive.size else 1.0

    repaired_count = 0
    for row in range(1, repaired.size - 1):
        previous_jump = repaired[row] - repaired[row - 1]
        following_jump = repaired[row + 1] - repaired[row]
        if previous_jump > 12.0 * 3600.0 and following_jump < -12.0 * 3600.0:
            repaired[row] = repaired[row - 1] + ordinary_step
            repaired_count += 1

    return repaired, repaired_count


def build_elapsed_time(
    frame: pd.DataFrame,
    date_column: str | None,
    time_column: str,
) -> tuple[pd.Series, pd.Series, str, int]:
    """Return elapsed seconds, timestamps, time-description, and repairs."""

    if date_column is not None:
        combined = (
            frame[date_column].astype(str).str.strip()
            + " "
            + frame[time_column].astype(str).str.strip()
        )
        timestamps = pd.to_datetime(combined, errors="coerce")
        valid = timestamps.notna()

        epoch_seconds = np.full(len(frame), np.nan, dtype=float)
        # Force nanosecond resolution before converting datetime values to
        # integers. Pandas 3 may preserve Excel timestamps as datetime64[us];
        # dividing those microsecond integers by 1e9 incorrectly produces
        # seconds/1000. Explicit datetime64[ns] conversion guarantees that the
        # resulting elapsed-time values are true seconds.
        timestamp_ns = (
            timestamps.loc[valid].to_numpy(dtype="datetime64[ns]").astype(np.int64)
        )
        epoch_seconds[valid.to_numpy()] = timestamp_ns / 1e9
        valid_seconds = epoch_seconds[valid.to_numpy()]
        valid_seconds, repaired_count = _repair_isolated_date_spikes(valid_seconds)
        epoch_seconds[valid.to_numpy()] = valid_seconds

        finite = np.isfinite(epoch_seconds)
        if np.count_nonzero(finite) < 3:
            raise ValueError(
                "Date and Time could not be converted to usable timestamps."
            )
        elapsed = epoch_seconds - epoch_seconds[finite][0]

        timestamp_strings = pd.Series("", index=frame.index, dtype=object)
        timestamp_strings.loc[valid] = timestamps.loc[valid].dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return (
            pd.Series(elapsed, index=frame.index),
            timestamp_strings,
            f"{date_column} + {time_column}",
            repaired_count,
        )

    numeric_time = pd.to_numeric(frame[time_column], errors="coerce")
    if numeric_time.notna().mean() >= 0.8:
        first = numeric_time.dropna().iloc[0]
        elapsed = numeric_time - first
        timestamp_strings = frame[time_column].astype(str)
        return elapsed, timestamp_strings, time_column, 0

    # Time-of-day text without a separate Date column. Reconstruct rollovers.
    timedeltas = pd.to_timedelta(
        frame[time_column].astype(str).str.strip(),
        errors="coerce",
    )
    seconds_of_day = timedeltas.dt.total_seconds().to_numpy(float)
    valid_count = np.count_nonzero(np.isfinite(seconds_of_day))
    if valid_count < 3:
        raise ValueError(
            "The selected time column is neither numeric elapsed time nor "
            "parseable time-of-day text. Select the Date column as well."
        )

    elapsed = np.full(seconds_of_day.shape, np.nan, dtype=float)
    day_offset = 0.0
    previous = np.nan
    first_value = np.nan
    for index, seconds in enumerate(seconds_of_day):
        if not np.isfinite(seconds):
            continue
        if np.isfinite(previous) and seconds < previous - 12.0 * 3600.0:
            day_offset += 24.0 * 3600.0
        absolute = seconds + day_offset
        if not np.isfinite(first_value):
            first_value = absolute
        elapsed[index] = absolute - first_value
        previous = seconds

    return (
        pd.Series(elapsed, index=frame.index),
        frame[time_column].astype(str),
        time_column,
        0,
    )


def read_log(path: str | Path) -> pd.DataFrame:
    """Read the first worksheet or CSV without changing the source file."""
    path = Path(path)
    frame = (
        pd.read_csv(path)
        if path.suffix.lower() == ".csv"
        else pd.read_excel(path, sheet_name=0)
    )
    frame.columns = [str(c).strip() for c in frame.columns]
    if frame.empty or frame.shape[1] < 2:
        raise ValueError("Provide a time column and at least one measurement column.")
    if frame.columns.duplicated().any():
        raise ValueError(
            "Column headers must be unique after removing surrounding spaces."
        )
    return frame


def interval_rows(
    data: pd.DataFrame, bounds: tuple[float, float], label: str
) -> tuple[int, int]:
    """Inclusive samples inside the requested bounds; never invent a nearest sample."""
    left, right = sorted(map(float, bounds))
    if not np.isfinite([left, right]).all():
        raise ValueError(f"{label}: interval bounds must be finite.")
    times = data["Time"].to_numpy(float)
    start = int(np.searchsorted(times, left, side="left"))
    end = int(np.searchsorted(times, right, side="right") - 1)
    if end - start + 1 < 3:
        raise ValueError(
            f"{label}: select an interval containing at least three time samples."
        )
    return start, end


def timestamp_at(data: pd.DataFrame, row: int) -> str:
    value = data["Timestamp"].iloc[row]
    return "" if pd.isna(value) else str(value)


def calculate_plateau(
    data: pd.DataFrame, channels: list[SignalChannel], bounds: tuple[float, float]
) -> PlateauSelection:
    """Arithmetic mean and sample SD (ddof=1), independently for each channel.

    This describes a user-selected interval; it does not assert equilibrium or
    test stationarity. Empty channels have mean=None, std=None and count=0.
    """
    start, end = interval_rows(data, bounds, "Plateau")
    values = {}
    for channel in channels:
        samples = data[channel.key].iloc[start : end + 1].to_numpy(float)
        samples = samples[np.isfinite(samples)]
        count = len(samples)
        values[channel.key] = PlateauValue(
            mean=float(np.mean(samples)) if count else None,
            std=float(np.std(samples, ddof=1)) if count >= 2 else None,
            count=count,
        )
    return PlateauSelection(
        float(data["Time"].iloc[start]),
        float(data["Time"].iloc[end]),
        start,
        end,
        timestamp_at(data, start),
        timestamp_at(data, end),
        values,
    )


def create_selection(
    data: pd.DataFrame,
    channels: list[SignalChannel],
    number: int,
    pulse_bounds: tuple[float, float],
    plateau_bounds: tuple[float, float],
    pre_context_s: float = 120,
) -> PulseSelection:
    """Capture both intervals atomically. Plateau may be outside the decay interval."""
    start_row, end_row = interval_rows(data, pulse_bounds, "Pulse")
    if not np.isfinite(pre_context_s) or pre_context_s < 0:
        raise ValueError("Pre-opening context must be a finite nonnegative duration.")
    plateau = calculate_plateau(data, channels, plateau_bounds)
    times = data["Time"].to_numpy(float)
    start, end = float(times[start_row]), float(times[end_row])
    context_row = int(np.searchsorted(times, start - pre_context_s, side="left"))
    return PulseSelection(
        number=number,
        start_time_s=start,
        end_time_s=end,
        start_row=start_row,
        end_row=end_row,
        context_start_time_s=float(times[context_row]),
        context_start_row=context_row,
        start_timestamp=timestamp_at(data, start_row),
        end_timestamp=timestamp_at(data, end_row),
        context_start_timestamp=timestamp_at(data, context_row),
        plateau=plateau,
    )
