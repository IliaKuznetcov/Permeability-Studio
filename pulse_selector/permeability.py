"""Ideal-gas pulse analysis: late-time estimates and inversion of the verified FDM.

All calculations use SI units and absolute pressures. No Qt dependency.
The original fdm_solver.py is kept unchanged; this module adds validation,
time-scaling for efficient fixed-slip inversion, and experimental data handling.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from threading import Event
import numpy as np
from scipy.optimize import brentq, least_squares

from .core import display_values
from .fdm_solver import run_fdm

MICRODARCY_M2 = 9.869233e-19
SOLVER_SHA = "815e3aa07cbfe838f2e735ae6133b34026be2175"
TIME_TOLERANCE_S = 1e-6  # Time controls display six decimal places.


@dataclass(frozen=True)
class AnalysisSettings:
    upstream_key: str
    downstream_key: str
    length_m: float
    diameter_m: float
    porosity: float
    upstream_volume_m3: float
    downstream_volume_m3: float
    viscosity_pa_s: float
    fit_start_s: float
    fit_end_s: float
    late_start_s: float
    late_end_s: float
    slip_pa: float = 0.0
    initial_k_m2: float = 1e-17
    min_k_m2: float = 1e-21
    max_k_m2: float = 1e-14
    cells: int = 40
    safety_factor: float = 0.8
    sigma_up_pa: float = 100.0
    sigma_down_pa: float = 100.0
    delta_floor_pa: float = 100.0
    max_steps: int = 250000
    # Legacy sessions retain their original, fully precharged storage assumption.
    initial_upstream_mode: str = 'Combined storage'
    upstream_dead_volume_m3: float = 0.0
    dead_pressure_mode: str = 'Downstream initial'
    initial_dead_pressure_pa: float = 101325.0
    upstream_pressure_correction_pa: float = 0.0
    downstream_pressure_correction_pa: float = 0.0
    fdm_backend: str = 'Reference explicit'

    def validate(self):
        values = asdict(self)
        if self.fdm_backend not in ('Reference explicit','Adaptive BDF'):
            raise ValueError('Choose Reference explicit or Adaptive BDF for the FDM solver.')
        if not all(np.isfinite(v) for v in values.values() if not isinstance(v, str)):
            raise ValueError("Analysis inputs must be finite.")
        if self.upstream_key == self.downstream_key:
            raise ValueError("Choose different upstream and downstream pressure channels.")
        for name in ('length_m', 'diameter_m', 'upstream_volume_m3', 'downstream_volume_m3',
                     'viscosity_pa_s', 'sigma_up_pa', 'sigma_down_pa', 'delta_floor_pa'):
            if values[name] <= 0:
                raise ValueError(f"{name} must be positive.")
        if not 0 < self.porosity < 1 or self.slip_pa < 0:
            raise ValueError("Porosity must be between 0 and 1; slip factor must be nonnegative.")
        if not 4 <= self.cells <= 400 or int(self.cells) != self.cells:
            raise ValueError("Use an integer number of intervals between 4 and 400.")
        if not 0 < self.safety_factor <= 0.8 or self.max_steps < 100 or int(self.max_steps)!=self.max_steps:
            raise ValueError("Use safety factor in (0, 0.8] and a valid step budget.")
        if not 0 < self.min_k_m2 < self.initial_k_m2 < self.max_k_m2:
            raise ValueError("Require 0 < minimum k < initial k < maximum k.")
        for a, b in ((self.fit_start_s, self.fit_end_s), (self.late_start_s, self.late_end_s)):
            if not 0 <= a < b:
                raise ValueError("Fit intervals must have 0 <= start < end, in seconds from valve opening.")
        validate_initial_storage(self)


def validate_initial_storage(settings):
    """Validate valve topology independently of permeability and entered porosity."""
    if settings.initial_upstream_mode not in ('Combined storage', 'Isolated tank'):
        raise ValueError('Choose the initial upstream storage arrangement.')
    if settings.dead_pressure_mode not in ('Downstream initial', 'Entered absolute pressure'):
        raise ValueError('Choose the initial pressure source for DU.')
    du, vu = settings.upstream_dead_volume_m3, settings.upstream_volume_m3
    if not np.isfinite([du, vu, settings.initial_dead_pressure_pa]).all() or vu <= 0 or not 0 <= du < vu:
        raise ValueError('Require 0 ≤ DU < connected upstream volume VU+DU.')
    if settings.initial_upstream_mode == 'Isolated tank' and du <= 0:
        raise ValueError('Enter upstream dead volume DU or use the linked calibration for the isolated-tank arrangement.')
    if settings.dead_pressure_mode == 'Entered absolute pressure' and settings.initial_dead_pressure_pa <= 0:
        raise ValueError('Initial DU pressure must be positive and absolute.')


def initial_upstream_state(trace, settings):
    """Inventory-preserving ideal/isothermal mixing before resolved core flow.

    Raw trace.initial_upstream_pa remains the pre-opening tank plateau for Boyle
    balance. The FDM instead starts with the mixed VU+DU pressure at valve t=0.
    """
    validate_initial_storage(settings)
    du = settings.upstream_dead_volume_m3 if settings.initial_upstream_mode == 'Isolated tank' else 0.0
    charged = settings.upstream_volume_m3-du
    pdu = (trace.initial_downstream_pa if settings.dead_pressure_mode == 'Downstream initial'
           else settings.initial_dead_pressure_pa)
    mixed = (charged*trace.initial_upstream_pa+du*pdu)/settings.upstream_volume_m3
    return dict(mode=settings.initial_upstream_mode, charged_volume_m3=float(charged),
                dead_volume_m3=float(du), connected_upstream_volume_m3=float(settings.upstream_volume_m3),
                tank_plateau_pa=float(trace.initial_upstream_pa), initial_dead_pressure_pa=float(pdu),
                mixed_upstream_pa=float(mixed), dead_pressure_mode=settings.dead_pressure_mode)


@dataclass(frozen=True)
class PulseTrace:
    time_s: np.ndarray
    upstream_pa: np.ndarray
    downstream_pa: np.ndarray
    initial_upstream_pa: float
    initial_downstream_pa: float
    final_upstream_pa: float
    final_downstream_pa: float
    warnings: tuple[str, ...] = ()
    pressure_error: str = ''


def validate_absolute_states(trace):
    """Boyle balance uses state means; a negative gauge mean is permitted."""
    for name,value in (('Initial upstream',trace.initial_upstream_pa),
                       ('Initial downstream/core',trace.initial_downstream_pa),
                       ('Final upstream',trace.final_upstream_pa),
                       ('Final downstream',trace.final_downstream_pa)):
        if not np.isfinite(value) or value<=0:
            raise ValueError(absolute_pressure_error(name,value))


def absolute_pressure_error(name,value):
    return (f'{name}: {value/1000:.7g} kPa abs ({value/6894.757293168:.7g} psia). '
            'Absolute pressure must be positive; negative gauge pressure is allowed. '
            'Check Gauge/Absolute basis, source units, measured barometric pressure and sensor zero. '
            'For gauge data: Pabs = Pgauge + Pbarometric + sensor correction. '
            'Use a correction only from an independent calibration/reference; do not raise barometric pressure or clip readings to force a fit.')


def extract_trace(project, pulse, settings, *, preview=False):
    """Read only the captured pulse's own snapshot, never the latest shared log."""
    if not preview:
        settings.validate()
    snapshot = project.snapshots[pulse.dataset_id]
    channels = {c.key: c for c in snapshot.channels}
    for key in (settings.upstream_key, settings.downstream_key):
        if key not in channels or channels[key].quantity != 'Pressure':
            raise ValueError("Assign two pressure channels from this pulse's recording.")
    if project.pressure_basis == 'Gauge':
        bar = project.barometric_kpa
        if bar is None or not np.isfinite(bar) or bar <= 0:
            raise ValueError("Enter measured barometric pressure for gauge recordings.")
        offset = bar * 1000
    elif project.pressure_basis == 'Absolute':
        offset = 0.0
    else:
        raise ValueError("Choose Gauge or Absolute pressure basis.")
    data = snapshot.data.iloc[pulse.decay_start_row:pulse.decay_end_row + 1]
    t = data.Time.to_numpy(float) - pulse.valve_time_s
    arrays = []
    corrections=(settings.upstream_pressure_correction_pa,settings.downstream_pressure_correction_pa)
    if not np.isfinite(corrections).all():
        raise ValueError('Pressure sensor corrections must be finite.')
    for key,correction in zip((settings.upstream_key, settings.downstream_key),corrections):
        arrays.append(display_values(data[key].to_numpy(float), channels[key], 'kPa') * 1000 + offset + correction)
    if len(t) < 5 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0) or t[0] < 0:
        raise ValueError("A pulse needs at least five samples at increasing, nonnegative valve-relative times.")
    pressure_error=''
    for key,p in zip((settings.upstream_key,settings.downstream_key),arrays):
        if np.any(p[np.isfinite(p)] <= 0):
            index=int(np.flatnonzero(np.isfinite(p)&(p<=0))[0])
            pressure_error=absolute_pressure_error(f'{channels[key].source_column}, valve t={t[index]:.7g} s',p[index])
            if not preview:
                raise ValueError(pressure_error)
            break
    if not preview and max(settings.fit_end_s, settings.late_end_s) > t[-1] + TIME_TOLERANCE_S:
        raise ValueError("Analysis intervals must end within the captured transient.")
    initial = [pulse.initial[k].mean * 1000 + offset + c for k,c in zip((settings.upstream_key, settings.downstream_key),corrections)]
    final = [pulse.final[k].mean * 1000 + offset + c for k,c in zip((settings.upstream_key, settings.downstream_key),corrections)]
    temporary=PulseTrace(t,*arrays,*initial,*final)
    try:
        validate_absolute_states(temporary)
    except ValueError as exc:
        if not preview:raise
        pressure_error=str(exc)
    if initial[0] <= initial[1]:
        raise ValueError("This implementation requires initial upstream pressure > downstream pressure. Check channel assignments.")
    warnings = ["Ideal gas, constant viscosity and isothermal flow; core initially at downstream pressure."]
    if any(corrections):
        warnings.append(f'User-entered pressure corrections: upstream {corrections[0]:.7g} Pa; downstream {corrections[1]:.7g} Pa. Corrections apply to both plateaus and transient; verify calibration independently.')
    for c in snapshot.channels:
        if c.quantity == 'Temperature':
            delta = pulse.final[c.key].mean - pulse.initial[c.key].mean
            if abs(delta) > 1:
                warnings.append(f"{c.source_column}: initial/final temperature changes by {delta:.3g} °C; review isothermal assumption.")
    if not any(c.quantity == 'Temperature' for c in snapshot.channels):
        warnings.append("No temperature channel: the isothermal assumption cannot be checked from this recording.")
    if (initial[0] - initial[1]) / np.mean(initial) > 0.1:
        warnings.append("Large initial pressure pulse: late-time linearization and Darcy-only transport require review.")
    return PulseTrace(t, *arrays, *initial, *final, tuple(warnings),pressure_error)


def storage_eigenvalue(a, c):
    """First nonzero Dicker–Smits root, using a continuous equation without poles.

    (q²-ac) sin(q) - (a+c) q cos(q) = 0.
    Divide by q to exclude the trivial zero root. First root is in (0, pi).
    """
    if not np.isfinite([a, c]).all() or min(a, c) <= 0:
        raise ValueError("Storage ratios must be finite and positive.")
    def determinant(q):
        return (q*q-a*c) * np.sinc(q/np.pi) - (a+c)*np.cos(q)
    return float(brentq(determinant, 0, np.pi, xtol=1e-14))


def late_time(trace, settings):
    """Regress ln(delta P / initial delta P) on the explicitly chosen tail.

    Returns both Brace (negligible core storage) and Dicker–Smits estimates.
    No pressure offset or drift is fitted; low/nonpositive differences are excluded.
    """
    settings.validate()
    validate_absolute_states(trace)
    if trace.pressure_error:raise ValueError(trace.pressure_error)
    t = trace.time_s
    dp = trace.upstream_pa - trace.downstream_pa
    interval = ((t >= settings.late_start_s-TIME_TOLERANCE_S) &
                (t <= settings.late_end_s+TIME_TOLERANCE_S))
    valid = interval & np.isfinite(dp) & (dp > settings.delta_floor_pa)
    if valid.sum() < 5:
        raise ValueError("Late-time interval needs at least five finite differential pressures above the noise floor.")
    x = t[valid]
    normalization = trace.initial_upstream_pa - trace.initial_downstream_pa
    y = np.log(dp[valid] / normalization)
    slope, intercept = np.polyfit(x, y, 1)
    if slope >= 0:
        raise ValueError("Selected late-time pressure difference is not decaying. Choose a different interval.")
    predicted = slope*x + intercept
    sse = float(np.sum((y-predicted)**2))
    sst = float(np.sum((y-y.mean())**2))
    if sst <= 0:
        raise ValueError("Late-time interval has no measurable decay.")
    p_ref = float(np.mean((trace.upstream_pa[valid]+trace.downstream_pa[valid])/2))
    area = np.pi * settings.diameter_m**2 / 4
    vp = settings.porosity * area * settings.length_m
    a, c = vp/settings.upstream_volume_m3, vp/settings.downstream_volume_m3
    q = storage_eigenvalue(a, c)
    factor = q*q/(a+c)
    brace = -slope * settings.viscosity_pa_s * settings.length_m / (
        area * p_ref * (1/settings.upstream_volume_m3 + 1/settings.downstream_volume_m3))
    corrected = brace/factor
    warnings = list(trace.warnings)
    if 1-sse/sst < 0.98:
        warnings.append("Late-time R² is below 0.98; inspect curvature and tail selection.")
    excluded = int(interval.sum()-valid.sum())
    if excluded:
        warnings.append(f"Excluded {excluded} missing/nonpositive/noise-floor late-time samples.")
    fraction = float(np.max(dp[valid]/((trace.upstream_pa[valid]+trace.downstream_pa[valid])/2)))
    if fraction > 0.1:
        warnings.append("DeltaP/Pmean exceeds 10% inside the chosen tail; small-perturbation approximation may be poor.")
    # Split-tail slopes help reveal curvature hidden by a high overall R².
    mid = len(x)//2
    first = float(np.polyfit(x[:mid], y[:mid], 1)[0]) if mid >= 3 else None
    last = float(np.polyfit(x[mid:], y[mid:], 1)[0]) if len(x)-mid >= 3 else None
    if first is not None and last is not None and abs(first-last)/abs(slope) > 0.2:
        warnings.append("First/second half tail slopes differ by >20%; a single late-time mode is not established.")
    return dict(method='Dicker–Smits and Brace, ideal-gas linearization',
                k_apparent_m2=float(corrected), k_apparent_microdarcy=float(corrected/MICRODARCY_M2),
                brace_microdarcy=float(brace/MICRODARCY_M2), slope_per_s=float(slope),
                intercept=float(intercept), r_squared=float(1-sse/sst),
                slope_standard_error=float(np.sqrt((sse/(len(x)-2))/np.sum((x-x.mean())**2))),
                mean_absolute_pa=p_ref, theta_1=q, storage_factor=float(factor),
                a=float(a), c=float(c), samples=int(valid.sum()), excluded=excluded,
                time_s=x.tolist(), log_normalized_delta=y.tolist(),
                predicted_log_delta=predicted.tolist(), warnings=warnings)


class CancelledError(RuntimeError):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError("Analysis cancelled; no result was saved.")


def simulate(trace, settings, k, *, cancel=None):
    """Run the selected integrator after shared physical-input validation."""
    _check_cancel(cancel)
    settings.validate()
    validate_absolute_states(trace)
    if trace.pressure_error:raise ValueError(trace.pressure_error)
    if not np.isfinite(k) or k <= 0:
        raise ValueError("Permeability must be finite and positive.")
    state = initial_upstream_state(trace, settings)
    initial_up = state['mixed_upstream_pa']
    if initial_up <= trace.initial_downstream_pa:
        raise ValueError('Mixed upstream pressure must exceed initial downstream pressure for this forward pulse model.')
    if settings.fdm_backend=='Adaptive BDF':
        from .adaptive_fdm import run_adaptive_fdm
        return run_adaptive_fdm(V_up=settings.upstream_volume_m3,V_down=settings.downstream_volume_m3,
            L=settings.length_m,D=settings.diameter_m,porosity=settings.porosity,k=k,
            mu=settings.viscosity_pa_s,b=settings.slip_pa,P_up_initial=initial_up,
            P_down_initial=trace.initial_downstream_pa,N=settings.cells,
            t_end=float(trace.time_s[-1]),max_evaluations=settings.max_steps,
            check_cancel=lambda:_check_cancel(cancel))
    dx = settings.length_m/settings.cells
    dt = settings.safety_factor*settings.porosity*settings.viscosity_pa_s*dx**2 / (
        2*k*(max(initial_up, trace.initial_downstream_pa)+settings.slip_pa))
    steps = int(np.ceil(trace.time_s[-1]/dt))
    if steps > settings.max_steps:
        raise ValueError(f"Forward simulation needs {steps:,} explicit steps (limit {settings.max_steps:,}). "
                         "Increase Maximum FDM steps per solve for this calculation, or use fewer spatial intervals/a shorter captured transient. "
                         "Cost grows with trial k, pressure and intervals², and decreases with porosity. "
                         "Keep physical inputs measured; check grid sensitivity after a coarse-grid trial.")
    result = run_fdm(V_up=settings.upstream_volume_m3, V_down=settings.downstream_volume_m3,
        L=settings.length_m, D=settings.diameter_m, porosity=settings.porosity, k=k,
        mu=settings.viscosity_pa_s, b=settings.slip_pa,
        P_up_initial=initial_up, P_down_initial=trace.initial_downstream_pa,
        N=settings.cells, t_end=float(trace.time_s[-1]), safety_factor=settings.safety_factor)
    _check_cancel(cancel)
    return result


def _reservoir_prediction(result,times):
    if 'evaluate' in result:
        return result['evaluate'](times)
    return np.array([np.interp(times,result['time'],result[name]) for name in ('P_up','P_down')])


def history_match(trace, settings, *, cancel=None, progress=None):
    """Fit one positive permeability with the Klinkenberg b held at its entered value.

    At fixed b, every reference-solver state is invariant under k*t scaling,
    including its stability timestep. Cache the full curve at the largest trial
    k so subsequent residual evaluations only interpolate it. The final result
    is recomputed with the selected backend at fitted k, independently of the
    cache. Adaptive curves use the integrator's dense interpolation.
    """
    settings.validate()
    t = trace.time_s
    window = ((t >= settings.fit_start_s-TIME_TOLERANCE_S) &
              (t <= settings.fit_end_s+TIME_TOLERANCE_S))
    masks = [window & np.isfinite(p) for p in (trace.upstream_pa, trace.downstream_pa)]
    if any(m.sum() < 5 for m in masks):
        raise ValueError("FDM interval needs at least five finite observations on each pressure channel.")
    cache_k, cache = 0.0, None
    evaluations = 0
    def residual(x):
        nonlocal cache_k, cache, evaluations
        _check_cancel(cancel)
        k = float(np.exp(x[0]))
        evaluations += 1
        if progress:
            progress(f"FDM evaluation {evaluations}: k={k/MICRODARCY_M2:.5g} µD")
        if cache is None or k > cache_k*(1+1e-12):
            # Cover nearby optimizer trials with one solve rather than rerunning
            # the reference integration for every finite-difference perturbation.
            cache_k = min(k*1.5, settings.max_k_m2)
            try:
                cache = simulate(trace, settings, cache_k, cancel=cancel)
            except ValueError as exc:
                if 'explicit steps' not in str(exc):
                    raise
                cache_k = k
                cache = simulate(trace, settings, k, cancel=cancel)
        prediction_times = t*k/cache_k
        predictions=_reservoir_prediction(cache,prediction_times)
        return np.concatenate([
            (prediction[m]-observed[m])/sigma
            for m, prediction, observed, sigma in zip(masks, predictions,
                (trace.upstream_pa, trace.downstream_pa), (settings.sigma_up_pa, settings.sigma_down_pa))])
    fit = least_squares(residual, [np.log(settings.initial_k_m2)],
        bounds=([np.log(settings.min_k_m2)], [np.log(settings.max_k_m2)]),
        diff_step=1e-4, max_nfev=40, ftol=1e-8, xtol=1e-8, gtol=1e-8)
    if not fit.success:
        raise ValueError(f"FDM fit did not converge: {fit.message}")
    k = float(np.exp(fit.x[0]))
    direct = simulate(trace, settings, k, cancel=cancel)
    pu,pd = _reservoir_prediction(direct,t)
    residuals = [pred-obs for pred, obs in zip((pu,pd),(trace.upstream_pa, trace.downstream_pa))]
    rmse = [float(np.sqrt(np.mean(r[m]**2))) for r,m in zip(residuals,masks)]
    area = np.pi*settings.diameter_m**2/4
    vp = settings.porosity*area*settings.length_m
    initial_state = initial_upstream_state(trace, settings)
    equilibrium = (settings.upstream_volume_m3*initial_state['mixed_upstream_pa'] +
        (settings.downstream_volume_m3+vp)*trace.initial_downstream_pa)/(
        settings.upstream_volume_m3+settings.downstream_volume_m3+vp)
    observed_final = (trace.final_upstream_pa+trace.final_downstream_pa)/2
    p_ref = float((np.mean(trace.upstream_pa[masks[0]])+
                   np.mean(trace.downstream_pa[masks[1]]))/2)
    warnings = list(trace.warnings)
    if initial_state['dead_volume_m3'] > 0:
        warnings.append('FDM approximates instantaneous isothermal VU–DU mixing with no core flow during mixing. Exclude valve-opening artifacts from the fit; simulation still starts at valve t=0. DU and core initially share the assumed core temperature.')
    warnings.append("One pulse fits k with b fixed; this does not independently identify intrinsic permeability and slip.")
    if direct['max_inventory_error'] > 1e-3:
        warnings.append("FDM inventory drift exceeds 0.1%; refine the spatial grid/timestep and check sensitivity before interpreting this fit.")
    if np.any(np.abs(fit.x[0]-np.log([settings.min_k_m2,settings.max_k_m2])) < 0.02):
        warnings.append("Permeability reached a search bound; review bounds and model adequacy.")
    if abs(equilibrium-observed_final) > max(3*max(settings.sigma_up_pa,settings.sigma_down_pa),0.01*observed_final):
        warnings.append("Predicted equilibrium differs from the selected final state. Check connected volumes, porosity, initial core pressure, leaks, temperature, and whether equilibrium was reached.")
    # Compare cached scaling against the fresh direct run inside the fit interval.
    scaled_up,scaled_down = _reservoir_prediction(cache,t*k/cache_k)
    interpolation_difference = float(max(np.max(abs(scaled_up-pu)),np.max(abs(scaled_down-pd))))
    if interpolation_difference > min(settings.sigma_up_pa,settings.sigma_down_pa)*0.1:
        warnings.append("Time interpolation error is material relative to entered pressure noise; reduce the timestep safety factor.")
    return dict(method=f'{settings.fdm_backend} FDM, fixed-slip single-pulse least squares',
        fdm_backend=settings.fdm_backend,adaptive_diagnostics={key:direct[key] for key in
            ('engine_version','nfev','njev','nlu','rtol','atol_pa') if key in direct},
        solver_blob_sha=SOLVER_SHA, initial_storage=initial_state,
        fitted_k_m2=k, fitted_k_microdarcy=k/MICRODARCY_M2,
        fixed_slip_pa=settings.slip_pa, apparent_at_mean_microdarcy=k*(1+settings.slip_pa/p_ref)/MICRODARCY_M2,
        mean_absolute_pa=p_ref, rmse_upstream_pa=rmse[0], rmse_downstream_pa=rmse[1],
        normalized_cost=float(fit.cost), evaluations=evaluations,
        time_s=t.tolist(), simulated_upstream_pa=pu.tolist(), simulated_downstream_pa=pd.tolist(),
        upstream_residual_pa=_finite_list(residuals[0]), downstream_residual_pa=_finite_list(residuals[1]),
        predicted_continuous_equilibrium_pa=float(equilibrium), observed_final_mean_pa=float(observed_final),
        max_inventory_error=float(direct['max_inventory_error']), dx_m=float(direct['dx']),
        dt_s=float(direct['dt']) if direct['dt'] is not None else None, steps=int(direct['num_steps']),
        scaling_check_max_pa=interpolation_difference, warnings=warnings)


def _finite_list(values):
    return [float(v) if np.isfinite(v) else None for v in values]


def analysis_fingerprint(project, pulse, settings):
    """Changing bounds, states, recording, pressure basis or inputs invalidates a fit."""
    snapshot = project.snapshots[pulse.dataset_id]
    # The stored dataset_id alone is insufficient if a session was externally
    # edited. Include the actual retained transient and channel definitions.
    # Decimal serialization is stable across our session JSON round trip.
    samples = snapshot.data.iloc[pulse.context_start_row:pulse.decay_end_row+1][
        ['Time'] + [c.key for c in snapshot.channels]]
    # Loading a session normalizes Time to float; 60 and 60.0 are the same sample.
    samples=samples.astype(float)
    sample_hash = sha256(samples.to_json(orient='split',double_precision=10).encode()).hexdigest()
    signature = dict(pulse=asdict(pulse), settings=asdict(settings),
        basis=project.pressure_basis, barometric_kpa=project.barometric_kpa,
        solver=SOLVER_SHA, sample_hash=sample_hash,
        channels=[asdict(c) for c in snapshot.channels])
    if settings.fdm_backend=='Adaptive BDF':
        from .adaptive_fdm import ENGINE_VERSION,RTOL,ATOL_PA
        signature['adaptive_integrator']=dict(version=ENGINE_VERSION,rtol=RTOL,atol_pa=ATOL_PA)
    return sha256(json.dumps(signature, sort_keys=True, allow_nan=False).encode()).hexdigest()


def fit_klinkenberg(records):
    """Cross-pulse regression k_app = k_infinity + k_infinity*b/Pmean.

    Caller must select comparable pulses from one sample with consistent geometry.
    This is a late-time regression, not joint nonlinear FDM inversion.
    """
    if len(records) < 3:
        raise ValueError("At least three accepted late-time estimates are required.")
    p = np.array([r['mean_absolute_pa'] for r in records])
    k = np.array([r['k_apparent_m2'] for r in records])
    if not np.isfinite(p).all() or not np.isfinite(k).all() or min(p) <= 0 or min(k) <= 0:
        raise ValueError("Klinkenberg inputs must be finite and positive.")
    if np.ptp(p)/p.mean() < 0.1:
        raise ValueError("Mean pressure span is below 10%; slip is poorly constrained.")
    # Scale 1/P to avoid badly conditioned SI regressions.
    x = 1e6/p
    slope, intercept = np.polyfit(x,k,1)
    if intercept <= 0 or slope < 0:
        raise ValueError("Regression gives nonphysical intrinsic permeability or negative slip. Inspect accepted pulses.")
    prediction = intercept+slope*x
    return dict(intrinsic_k_m2=float(intercept), intrinsic_microdarcy=float(intercept/MICRODARCY_M2),
        slip_pa=float(slope/intercept*1e6), samples=len(records),
        r_squared=float(1-np.sum((k-prediction)**2)/np.sum((k-k.mean())**2)) if np.ptp(k)>0 else None)
