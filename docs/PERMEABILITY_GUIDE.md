> The adaptive BDF and explicit finite-difference solvers are documented in [ADAPTIVE_FDM.md](ADAPTIVE_FDM.md).

# Permeability analysis

This guide describes permeability inversion of captured pulses.
Start with one well-behaved captured pulse. Fit permeability while holding volumes,
porosity, viscosity, geometry and the Klinkenberg factor fixed. Compare the pressure
curves, residuals and late-time estimate before applying it to more pulses.

## Analyze an experimental pulse

- Open a saved paired-pulse session or capture a new pulse. Choose initial states
  **before the actual valve event**, final states after it, and preserve the full
  transient. The suggested midpoint between regions is not event detection.
- Select its row. Analysis uses that pulse's retained recording, even after you
  import a newer log. An uncommitted draft is not analyzed.
- Assign upstream and downstream pressure channels explicitly. Suggestions only
  use an unambiguous Upstream/Downstream location mapping. Review those mappings.
- For gauge readings, enter measured barometric pressure in the Pulse picker.
  All model pressures are absolute. Raw source units may differ between channels.
- Enter measured core dimensions, porosity, gas viscosity and connected volumes.
  Default teaching values are not your apparatus measurements. Porosity is a
  fraction, not a percentage. Viscosity must correspond to your gas and temperature.
- With the same volume boundaries as the check-plug calibration, connected volumes
  are **VU + DU** and **VD + DD**, excluding core pore volume. The calibration button
  uses those sums; it does not divide an unallocated dead volume in half. Confirm
  that the pulse valve configuration actually connects those volumes.
- Select **Isolated tank** when only VU was charged before opening; the calibration
  button also fills DU. Check DU's initial pressure source. The adapter starts
  the FDM from ideal/isothermal mixed pressure [VU*Pui+DU*Pdui]/(VU+DU), with
  instantaneous mixing and negligible core flow during mixing assumed. The
  pre-opening plateau remains available for the separate Boyle balance.
  **Combined storage** assumes that all VU+DU started at Pui.
- Choose separate fitting intervals in seconds from valve opening. The shaded
  pressure interval chooses FDM observations; the draggable semilog interval chooses
  the late-time regression. FDM still simulates from valve t=0. It is never
  restarted from the first observation in a delayed fitting window.
- Start with b=0 for an effective/apparent gas-permeability fit. Enter an independently
  determined nonnegative b to estimate intrinsic k **conditional on that b**.
  A single fit does not independently establish k and b.
- Set pressure noise scales on both channels. They weight the residuals; they are
  not inferred uncertainty bounds on the fitted permeability. Each finite sample
  contributes once; irregular sampling is retained and not resampled.
- Inspect both pressure curves and residuals. A good differential-pressure fit
  alone can conceal an incorrect common pressure/equilibrium.
- Repeat the fit at a finer spatial grid and/or smaller timestep safety factor
  before reporting experimental permeability. The explicit solver has
  finite-grid initial-storage bias and small inventory drift.

A fit can converge while the physical assumptions are wrong. In particular,
permeability cannot fix the equilibrium implied by incorrect gas inventory.
The dialog reports predicted continuous equilibrium versus the selected final mean.
A final window which has not reached equilibrium can also explain that difference.

## What the late-time calculation does

It fits the explicitly selected tail using

$$
\ln\left(\frac{P_u(t)-P_d(t)}{P_{u,0}-P_{d,0}}\right)=c_0+m t
$$

Missing differences and values at or below the entered positive noise floor are
excluded and counted. At least five usable observations and a negative slope are
required. The intercept is fitted; no sensor-offset correction is fitted.

The Brace estimate assumes negligible core storage:

$$
k_{\mathrm{app,Brace}}=\frac{-m\,\mu L}{A\,P_{\mathrm{ref}}\left(1/V_u+1/V_d\right)}
$$

For ideal isothermal gas, P_ref is the mean absolute pressure in the chosen tail.
The Dicker–Smits storage correction uses

$$
V_p=\varepsilon A L,\qquad a=\frac{V_p}{V_u},\qquad c=\frac{V_p}{V_d}
$$

$$
(q^2-ac)\sin(q)-(a+c)q\cos(q)=0
$$

$$
f=\frac{q_1^2}{a+c},\qquad k_{\mathrm{app,corrected}}=\frac{k_{\mathrm{app,Brace}}}{f}
$$

The first positive root is solved on (0, pi) using a continuous determinant,
with the trivial q=0 root removed. This avoids tangent and rational-function poles
being misidentified as eigenvalues. Here c is the downstream storage ratio;
Klinkenberg b is an unrelated dimensional parameter.

The display includes R², slope, standard error of the regression slope, root,
storage correction and number of usable observations. It flags low R², different
first-half/second-half slopes, large pressure perturbations and temperature changes.
A straight line with high R² alone does not prove that the late-time assumptions hold.
No automatic optimum-tail selector is imposed.

These are small-perturbation, constant-property ideal-gas estimates, not a real-gas
pseudopressure calculation. See Dicker and Smits, SPE-17578-MS,
https://doi.org/10.2118/17578-MS. The source equation is also reproduced in
the open research article https://doi.org/10.1007/s13202-017-0376-5.

## What the FDM fit does

`pulse_selector/fdm_solver.py` is the finite-difference forward solver used by the application.

It solves ideal-gas, Darcy–Klinkenberg transport with phi=(P+b)^2, explicit time
integration, storage-consistent tank boundaries and a core initially at downstream
pressure. Both initial tank pressures come from the captured initial state windows.
Viscosity, porosity, volumes, geometry and b are fixed inputs. Temperature channels
are reviewed but not used to solve nonisothermal transport.

The inverse adapter minimizes upstream and downstream pressure residuals together,
each divided by its entered noise scale. It optimizes log(k), ensuring positive k,
within entered bounds. No time-zero shift, leak term, pressure offset, temperature
correction, porosity or volume is fitted.

At fixed b, k only scales time in this model. A cached reference curve therefore
makes the least-squares search efficient. The final solution is independently
rerun with the forward solver at the fitted k. The maximum discrepancy between
cached interpolation and that final direct run is reported.

A forward evaluation defaults to 250,000 explicit steps to bound runtime and
memory. The analysis form exposes **Maximum FDM steps per solve**;
it is saved per pulse. Raise that budget deliberately for a larger solve, or
reduce spatial intervals for exploration/shorten the captured transient.
Do not change measured physical inputs simply to bypass the resource limit.
For final reporting, perform a deliberate convergence study rather than relying
on a coarse grid. Calculations run in a Qt worker thread. Cancel is checked between
bounded forward evaluations; an in-progress reference solve completes first.

With **Adaptive BDF**, the same budget counts RHS evaluations, the safety factor
is unused, and error-controlled adaptive implicit stepping replaces explicit
Euler timesteps. Cancellation is checked within the adaptive solve. Dense output
retains the measured sample times..

## More than one pulse and Klinkenberg

Analyze one pulse at a time and save its late-time fit. Enter the same nonempty
Sample ID on comparable pulses. The Klinkenberg button combines at least three
current late-time estimates for that sample, requiring matching physical inputs,
R² >= 0.98 for each, and at least 10% mean-pressure span. It fits

$$
k_{\mathrm{app}}=k_\infty\left(1+\frac{b}{P_{\mathrm{mean}}}\right)
$$

Nonpositive intercepts and negative slip estimates are rejected. Review all tail
warnings yourself. This unweighted cross-pulse regression is **not joint FDM
inversion** and does not provide validated confidence intervals. Its b can be
entered for subsequent fixed-slip FDM fits. Joint nonlinear k/b fitting, uncertainty
propagation, real-gas EOS, thermal transport, FVM and fall-off are later extensions.

## Results, compatibility and reproducibility

- Settings and results are stored by pulse UID in `PulseProject.analysis_settings`
  and `analysis_results`. They are independent of `ApplicationModel.data`.
- Results are fingerprinted against the actual retained samples, pulse definition,
  channel units/mapping, pressure basis, barometric pressure, solver and inputs.
  Changing these invalidates a result; it must be recalculated before display/export.
- The existing XLSX pulse export retains its raw/state sheets. Complete computed
  analysis results are retained in pulse-session JSON or separate result JSON.
- For a no-GUI reproduction:

      python analyze_pulse_session.py examples/pulse_session.json result.json

  Use `--pulse "exact label"`, `--method late|fdm|both`, or `--settings inputs.json`.
  Settings JSON uses the SI field names in `AnalysisSettings`.

## Where the code lives

| Module | Responsibility |
|---|---|
| `permeability.py` | Unit-aware extraction, late-time methods, fingerprints, FDM inversion and Klinkenberg regression. |
| `fdm_solver.py` | Unmodified verified forward solver. |
| `permeability_view.py` | Analysis dialog, plots, worker, result persistence and JSON export. |
| `pulse_capture_controller.py` | Routes Analyze selected pulse to its captured record. |
| `pulse_capture_models.py` / `pulse_capture_io.py` | Session fields and compatible persistence. |
| `analyze_pulse_session.py` | Reproducible command-line analysis. |

## Verification boundary

The forward-solver checks pass: uniform pressure, zero permeability
and the baseline equilibrium/inventory regression. New tests cover the independent
symmetric eigenvalue condition, analytical exponential-tail recovery, fixed-slip
FDM recovery with delayed observations/missing values, cross-pressure Klinkenberg
recovery, cancellation/budgets, unit conversion, persistence and real dialog actions.
Numerical recovery is not, by itself, validation against a real experiment.
