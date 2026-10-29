"""Independent check-plug volume solver (no Qt, pandas, or file dialogs).

Mass balance: (A-C)*Vtank + (B-C)*Vreceive = 0,
A=Pi/(Zi*Ti), B=Pb/(Zb*Tb), C=Pf/(Zf*Tf).
Pressures: kPa absolute; temperatures: K; volumes: cm3.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .calibration_models import CalibrationRun

EOS_OPTIONS = ("Peng–Robinson nitrogen", "Ideal gas (Z=1)")
PARAMETERS = ("VU", "VD", "DU", "DD")


@dataclass(frozen=True)
class RunInputs:
    """One complete expansion: absolute kPa, kelvin, cm³ and dimensionless Z."""

    uid: str
    label: str
    side: str
    configuration: str
    plug_volume_cm3: float
    pi_kpa: float
    pb_kpa: float
    pf_kpa: float
    ti_k: float
    tb_k: float
    tf_k: float
    zi: float
    zb: float
    zf: float


@dataclass
class FitResult:
    """The four fitted volumes and the diagnostics shown/exported by the app."""

    eos: str
    labels: list[str]
    uids: list[str]
    volumes_cm3: dict[str, float]
    standard_errors_cm3: dict[str, float | None]
    covariance: list[list[float]] | None
    residuals: list[float]
    fitted_rhs: list[float]
    rhs: list[float]
    matrix: list[list[float]]
    closure_mmol: list[float]
    sse: float
    rmse: float
    r_squared: float | None
    rank: int
    condition_number: float
    degrees_of_freedom: int
    warnings: list[str]


def nitrogen_z(pressure_kpa: float, temperature_k: float) -> float:
    """Largest physical real Peng–Robinson root using the manual's N2 constants."""
    if (
        not np.isfinite([pressure_kpa, temperature_k]).all()
        or pressure_kpa < 0
        or temperature_k <= 0
    ):
        raise ValueError(
            "EOS requires nonnegative absolute pressure and positive kelvin temperature."
        )
    if pressure_kpa == 0:
        return 1.0
    # Nitrogen critical properties: temperature (K), pressure (kPa), acentric factor.
    tc, pc, omega = 126.192, 3395.800445, 0.0372
    kappa = 0.37464 + 1.54226 * omega - 0.26992 * omega**2
    alpha = (1 + kappa * (1 - np.sqrt(temperature_k / tc))) ** 2
    a = 0.45724 * alpha * (pressure_kpa / pc) * (tc / temperature_k) ** 2
    b = 0.07780 * (pressure_kpa / pc) * (tc / temperature_k)
    roots = np.roots([1.0, -(1 - b), a - 3 * b * b - 2 * b, -(a * b - b * b - b**3)])
    gas_roots = []
    for root in roots:
        if abs(root.imag) < 1e-8 and root.real > b:
            gas_roots.append(root.real)
    if not gas_roots:
        raise ValueError("No physical Peng–Robinson gas root was found.")
    return float(max(gas_roots))


def inputs_for_run(run: CalibrationRun, eos: str = EOS_OPTIONS[0]) -> RunInputs:
    """Turn captured ROI means and run conditions into gas-law inputs."""
    if eos not in EOS_OPTIONS:
        raise ValueError("Choose a supported equation of state.")
    problems = []
    if run.configuration == "Through-bore" and run.plug_volume_cm3 <= 0:
        problems.append("enter a positive known through-bore volume Vp")
    if run.pressure_basis == "Gauge" and run.barometric_kpa is None:
        problems.append("measured barometric pressure is missing")
    if run.receiving_mode == "Vented":
        pb = run.barometric_kpa
    else:
        pb = run.receiving_pressure_kpa
    if pb is None:
        problems.append("initial receiving absolute pressure is missing")
    if run.receiving_temperature_c is None:
        problems.append("receiving temperature Tb is missing")
    if not run.receiving_temperature_source.strip():
        problems.append("Tb source or assumption is missing")
    if not run.final_temperature_confirmed:
        problems.append(
            "confirm that the tank final temperature represents the connected final state"
        )
    if problems:
        raise ValueError(f"{run.label}: " + "; ".join(problems))
    # Gauge pressures need one atmospheric correction. Pb is already absolute.
    pressure_offset = 0.0
    if run.pressure_basis == "Gauge":
        pressure_offset = run.barometric_kpa
    pi = run.rois["Pi"].mean + pressure_offset
    pf = run.rois["Pf"].mean + pressure_offset

    if run.side == "Upstream":
        temperature_prefix = "Tu"
    else:
        temperature_prefix = "Td"
    ti = run.rois[temperature_prefix + "_i"].mean + 273.15
    tf = run.rois[temperature_prefix + "_f"].mean + 273.15
    tb = run.receiving_temperature_c + 273.15
    if (
        not np.isfinite([pi, pf, pb, ti, tf, tb]).all()
        or min(pi, pf, ti, tf, tb) <= 0
        or pb < 0
    ):
        raise ValueError(
            f"{run.label}: absolute pressures/kelvin temperatures are invalid."
        )
    # A manually supplied Z takes priority over the selected equation of state.
    if run.zi_override is not None:
        zi = run.zi_override
    elif eos == EOS_OPTIONS[0]:
        zi = nitrogen_z(pi, ti)
    else:
        zi = 1.0

    if run.zf_override is not None:
        zf = run.zf_override
    elif eos == EOS_OPTIONS[0]:
        zf = nitrogen_z(pf, tf)
    else:
        zf = 1.0
    if not np.isfinite([zi, zf, run.zb]).all() or min(zi, zf, run.zb) <= 0:
        raise ValueError(f"{run.label}: compressibility factors must be positive.")
    return RunInputs(
        uid=run.uid,
        label=run.label,
        side=run.side,
        configuration=run.configuration,
        plug_volume_cm3=run.plug_volume_cm3,
        pi_kpa=float(pi),
        pb_kpa=float(pb),
        pf_kpa=float(pf),
        ti_k=float(ti),
        tb_k=float(tb),
        tf_k=float(tf),
        zi=float(zi),
        zb=run.zb,
        zf=float(zf),
    )


def design_row(state: RunInputs):
    """Build one equation in the fixed column order VU, VD, DU, DD."""
    values = [
        state.pi_kpa,
        state.pb_kpa,
        state.pf_kpa,
        state.ti_k,
        state.tb_k,
        state.tf_k,
        state.zi,
        state.zb,
        state.zf,
    ]
    if (
        not np.isfinite(values).all()
        or state.pb_kpa < 0
        or min(
            state.pi_kpa,
            state.pf_kpa,
            state.ti_k,
            state.tb_k,
            state.tf_k,
            state.zi,
            state.zb,
            state.zf,
        )
        <= 0
    ):
        raise ValueError(
            "Run inputs require positive absolute tank/final pressures, positive K/Z, and nonnegative Pb."
        )
    # A, B and C are the P/(Z*T) coefficients in the calibration derivation.
    a = state.pi_kpa / (state.zi * state.ti_k)
    b = state.pb_kpa / (state.zb * state.tb_k)
    c = state.pf_kpa / (state.zf * state.tf_k)
    row = np.zeros(4)
    if state.side not in ("Upstream", "Downstream"):
        raise ValueError("Expansion side must be Upstream or Downstream.")
    if state.configuration not in (
        "Through-bore",
        "Solid isolating",
        "Side-only cavity",
    ):
        raise ValueError("Unknown plug configuration.")
    if state.plug_volume_cm3 < 0 or not np.isfinite(state.plug_volume_cm3):
        raise ValueError("Known plug volume must be nonnegative.")
    if state.configuration == "Solid isolating" and state.plug_volume_cm3 != 0:
        raise ValueError("Solid isolating plug volume must be zero.")
    if state.side == "Upstream":
        row[0] = a - c  # VU: isolated upstream tank
    else:
        row[1] = a - c  # VD: isolated downstream tank

    if state.configuration == "Through-bore":
        row[2:4] = b - c  # Both dead volumes connect through the bore.
    elif state.side == "Upstream":
        row[2] = b - c  # DU only
    else:
        row[3] = b - c  # DD only
    return row, (c - b) * state.plug_volume_cm3


def fit_volumes(states: list[RunInputs], eos: str = EOS_OPTIONS[0]) -> FitResult:
    """Fit VU, VD, DU and DD by unweighted least squares using SVD.

    The caller supplies complete, included runs. The steps below match the
    derivation: build the equations, solve them, then assess the residuals.
    """
    # 1. Check that the experiments can establish an absolute volume scale.
    if len(states) < 4:
        raise ValueError(
            "At least four independent, complete included runs are required."
        )
    if len({s.uid for s in states}) != len(states):
        raise ValueError("A run cannot be included more than once.")
    if not any(s.plug_volume_cm3 > 0 for s in states):
        raise ValueError(
            "Include known nonzero plug-volume runs to establish the absolute volume scale."
        )
    # 2. Each experiment contributes one row to X * volumes = y.
    rows = []
    rhs = []
    for state in states:
        row, right_hand_side = design_row(state)
        rows.append(row)
        rhs.append(right_hand_side)
    design_matrix = np.asarray(rows, dtype=float)
    measured_rhs = np.asarray(rhs, dtype=float)
    if not np.isfinite(design_matrix).all() or not np.isfinite(measured_rhs).all():
        raise ValueError("The design matrix contains invalid inputs.")

    # 3. Solve for the four volumes without imposing positivity constraints.
    volumes, _, rank, singular_values = np.linalg.lstsq(
        design_matrix, measured_rhs, rcond=None
    )
    if rank < 4:
        raise ValueError(
            "The calibration matrix has rank below four. Include independent runs from both sides "
            "and solid/side-only tests to separate DU and DD."
        )
    # 4. Compare the predicted and measured right-hand sides for the same runs.
    fitted_rhs = design_matrix @ volumes
    residuals = fitted_rhs - measured_rhs
    sse = float(residuals @ residuals)
    total_sum_squares = float(np.sum((measured_rhs - measured_rhs.mean()) ** 2))
    degrees_of_freedom = len(states) - 4
    covariance = None
    standard_errors = dict.fromkeys(PARAMETERS)
    if degrees_of_freedom > 0:
        _, singular_svd, vt = np.linalg.svd(design_matrix, full_matrices=False)
        covariance = (vt.T * (1 / singular_svd**2)) @ vt * (sse / degrees_of_freedom)
        for index, parameter in enumerate(PARAMETERS):
            variance = covariance[index, index]
            standard_errors[parameter] = float(np.sqrt(max(variance, 0)))
    condition_number = float(singular_values[0] / singular_values[-1])

    # 5. Report questionable results; do not silently modify the fit.
    warnings = []
    if np.any(volumes <= 0):
        warnings.append(
            "Nonpositive fitted volume: review inputs/topology. No positivity constraint was imposed."
        )
    if condition_number > 1e8:
        warnings.append("Numerically ill-conditioned matrix (condition number > 1e8).")
    if degrees_of_freedom == 0:
        warnings.append(
            "Exactly four runs: residual-based standard errors cannot be estimated."
        )
    for state in states:
        if not state.pb_kpa < state.pf_kpa < state.pi_kpa:
            warnings.append(
                f"{state.label}: expected Pb < Pf < Pi is not satisfied; review this expansion."
            )
    if eos == EOS_OPTIONS[0]:
        warnings.append(
            "Final nitrogen/initial-air mixture uses the nitrogen Z approximation from the manual."
        )
    covariance_list = None
    if covariance is not None:
        covariance_list = covariance.tolist()
    r_squared = None
    if total_sum_squares > 0:
        r_squared = 1 - sse / total_sum_squares

    return FitResult(
        eos=eos,
        labels=[state.label for state in states],
        uids=[state.uid for state in states],
        volumes_cm3=dict(zip(PARAMETERS, map(float, volumes))),
        standard_errors_cm3=standard_errors,
        covariance=covariance_list,
        residuals=residuals.tolist(),
        fitted_rhs=fitted_rhs.tolist(),
        rhs=measured_rhs.tolist(),
        matrix=design_matrix.tolist(),
        closure_mmol=(-residuals / 8.314462618).tolist(),
        sse=sse,
        rmse=float(np.sqrt(sse / len(states))),
        r_squared=r_squared,
        rank=int(rank),
        condition_number=condition_number,
        degrees_of_freedom=degrees_of_freedom,
        warnings=warnings,
    )


def fit_project_runs(
    runs: list[CalibrationRun], eos: str = EOS_OPTIONS[0]
) -> FitResult:
    """Validate exclusions, prepare the included runs, and call the solver."""
    # Validate exclusions first, before attempting to prepare any included run.
    for run in runs:
        if not run.included and not run.exclusion_reason.strip():
            raise ValueError(f"{run.label}: document an exclusion reason.")

    prepared_runs = []
    for run in runs:
        if run.included:
            prepared_runs.append(inputs_for_run(run, eos))
    return fit_volumes(prepared_runs, eos)
