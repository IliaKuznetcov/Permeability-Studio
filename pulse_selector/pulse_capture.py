"""Capture two state regions plus a valve reference and independent fitting interval."""

from __future__ import annotations
from uuid import uuid4
import numpy as np
from .core import interval_rows
from .state_extraction import extract_roi
from .pulse_capture_models import PairedPulse


def ordered(bounds):
    """Return a region's two endpoints in increasing time order."""
    values = sorted(map(float, bounds))
    if len(values) != 2 or not np.isfinite(values).all() or values[0] >= values[1]:
        raise ValueError("A region needs two finite times with nonzero width.")
    return tuple(values)


def suggested_valve_time(initial_bounds, final_bounds):
    """Suggest the midpoint of the gap; this does not detect a valve event."""
    initial = ordered(initial_bounds)
    final = ordered(final_bounds)
    if initial[1] >= final[0]:
        raise ValueError(
            "Initial and final pressure regions overlap. Separate them around one pulse."
        )
    return (initial[1] + final[0]) / 2


def capture_pulse(
    snapshot,
    *,
    label,
    valve_time_s,
    initial_pressure_bounds,
    final_pressure_bounds,
    initial_temperature_bounds=None,
    final_temperature_bounds=None,
    fit_bounds=None,
    pre_context_s=120.0,
    sample_id="",
    notes="",
    uid=None,
):
    """Build one pulse record from its state windows and original sample rows.

    The raw recording stays in the snapshot. This record stores its ID, state
    statistics and row boundaries, so exporting can recover every raw sample.
    """
    # 1. Validate the ID, valve marker and selected state windows.
    if not label.strip():
        raise ValueError("Enter a pulse ID.")
    if not np.isfinite(valve_time_s):
        raise ValueError("Choose the valve-opening time.")
    pressure_initial = ordered(initial_pressure_bounds)
    pressure_final = ordered(final_pressure_bounds)
    if initial_temperature_bounds is None:
        initial_temperature_bounds = pressure_initial
    if final_temperature_bounds is None:
        final_temperature_bounds = pressure_final
    temperature_initial = ordered(initial_temperature_bounds)
    temperature_final = ordered(final_temperature_bounds)

    pressures = []
    temperatures = []
    for channel in snapshot.channels:
        if channel.quantity == "Pressure":
            pressures.append(channel)
        elif channel.quantity == "Temperature":
            temperatures.append(channel)
    if not pressures:
        raise ValueError("Select at least one pressure channel for an actual pulse.")

    initial_regions = [("Initial pressure", pressure_initial)]
    final_regions = [("Final pressure", pressure_final)]
    if temperatures:
        initial_regions.append(("Initial temperature", temperature_initial))
        final_regions.append(("Final temperature", temperature_final))

    for name, bounds in initial_regions:
        if bounds[1] >= valve_time_s:
            raise ValueError(
                f"{name} ends at {bounds[1]:g} s, but valve opening is {valve_time_s:g} s. "
                "Move the marker after the initial region or move that region before the valve event."
            )
    for name, bounds in final_regions:
        if bounds[0] <= valve_time_s:
            raise ValueError(
                f"{name} starts at {bounds[0]:g} s, but valve opening is {valve_time_s:g} s. "
                "Move the marker before the final region or move that region after the valve event."
            )
    # 2. Calculate separate before/after statistics for every selected channel.
    initial_statistics = {}
    final_statistics = {}
    for channel in pressures + temperatures:
        if channel.quantity == "Pressure":
            initial_bounds = pressure_initial
            final_bounds = pressure_final
            initial_role = "Pi"
            final_role = "Pf"
        else:
            initial_bounds = temperature_initial
            final_bounds = temperature_final
            initial_role = "Ti"
            final_role = "Tf"

        initial_statistics[channel.key] = extract_roi(
            snapshot, channel.key, initial_bounds, initial_role
        )
        final_statistics[channel.key] = extract_roi(
            snapshot, channel.key, final_bounds, final_role
        )

    # 3. Retain the whole transient through the latest final-state endpoint.
    data = snapshot.data
    times = data["Time"].to_numpy(float)
    decay_end_s = pressure_final[1]
    if temperatures:
        decay_end_s = max(decay_end_s, temperature_final[1])
    decay_start, decay_end = interval_rows(
        data, (valve_time_s, decay_end_s), "Pulse transient"
    )

    # Context must include the full initial windows, even if they start earlier
    # than the requested pre-valve duration.
    if not np.isfinite(pre_context_s) or pre_context_s < 0:
        raise ValueError("Pre-opening context must be finite and nonnegative.")
    earliest_initial_time = pressure_initial[0]
    if temperatures:
        earliest_initial_time = min(earliest_initial_time, temperature_initial[0])
    context_start_s = min(earliest_initial_time, valve_time_s - pre_context_s)
    context_start = int(np.searchsorted(times, context_start_s, side="left"))

    # 4. A custom fit interval selects a subset; it never shortens the saved pulse.
    if fit_bounds is None:
        fit_bounds = (valve_time_s, decay_end_s)
    fit_start, fit_end = ordered(fit_bounds)
    if fit_start < valve_time_s or fit_end > decay_end_s:
        raise ValueError(
            "The fitting interval must be within valve opening through the selected final-state end."
        )
    fit_first, fit_last = interval_rows(data, (fit_start, fit_end), "Fitting interval")

    # Named fields make it clear which value is stored in each part of the record.
    return PairedPulse(
        uid=uid or str(uuid4()),
        label=label.strip(),
        dataset_id=snapshot.dataset_id,
        valve_time_s=float(valve_time_s),
        initial_pressure_bounds=pressure_initial,
        final_pressure_bounds=pressure_final,
        initial_temperature_bounds=temperature_initial,
        final_temperature_bounds=temperature_final,
        initial=initial_statistics,
        final=final_statistics,
        decay_start_row=decay_start,
        decay_end_row=decay_end,
        context_start_row=max(0, context_start),
        fit_start_s=fit_start,
        fit_end_s=fit_end,
        fit_start_row=fit_first,
        fit_end_row=fit_last,
        pre_context_s=float(pre_context_s),
        sample_id=sample_id.strip(),
        notes=notes.strip(),
    )


def pulse_review_notes(pulse, snapshot):
    """Report data-quality observations without changing the selected samples."""
    notes = []
    if (
        pulse.initial_temperature_bounds != pulse.initial_pressure_bounds
        or pulse.final_temperature_bounds != pulse.final_pressure_bounds
    ):
        if any(c.quantity == "Temperature" for c in snapshot.channels):
            notes.append("Pressure and temperature state windows differ.")
    for state_name, values in [("Initial", pulse.initial), ("Final", pulse.final)]:
        for stat in values.values():
            if stat.count != stat.total_count:
                notes.append(
                    f"{state_name} {stat.source_column}: {stat.count}/{stat.total_count} finite readings."
                )
    locations = {c.location for c in snapshot.channels if c.quantity == "Pressure"}
    if not {"Upstream", "Downstream"}.issubset(locations):
        notes.append(
            "Both upstream/downstream pressure locations are not assigned; review before two-tank history matching."
        )
    return notes
