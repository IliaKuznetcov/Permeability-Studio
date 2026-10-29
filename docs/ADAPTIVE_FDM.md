# Adaptive FDM inversion

## Select the time integrator

Choose **FDM time integrator → Adaptive BDF** in the pulse analysis form, then
run the FDM match. Existing saved settings retain **Reference explicit** until
changed. New, unsaved analyses suggest BDF. Save pulses to retain your selection.
Changing the backend invalidates older permeability fits; porosity calculations
are independent of the FDM time integrator.

The original `fdm_solver.py` remains unchanged. BDF uses SciPy already required
by the application. No JIT/compiler installation is needed.

## Why low porosity slows the reference solver

Its explicit stability timestep is proportional to

$$
\Delta t\propto\frac{\varepsilon\mu(L/N)^2}{k(P_{\max}+b)}
$$

At fixed other inputs, halving porosity approximately doubles the number of
steps, and doubling the spatial intervals approximately quadruples it. Raising
the allowed step budget permits more work but does not speed that work up.
The inversion already caches forward curves at fixed b using k*time scaling.

BDF selects implicit variable-order timesteps from local error estimates.
It resolves the early transient with smaller steps and can take larger steps
later without the explicit Euler stability restriction. Nonlinear iterations,
error control and the chosen spatial grid still matter.

## What is preserved and what changes

The PDE physics, fixed-b slip relation, grid locations, reservoir boundary
coefficients, external volumes, endpoint half-cell storage and initial node
values match the reference model. The valve mixing approximation is unchanged.

Let $U=(P+b)/S$, where $S=\max(P_{u,0},P_{d,0})+b$. Taking the continuous-time limit of the
reference transformed-pressure update and applying the chain rule gives

$$
\frac{dU_j}{dt}=\frac{kS}{2\varepsilon\mu\Delta x^2}
\left(U_{j+1}^2-2U_j^2+U_{j-1}^2\right)
$$

$$
\frac{dU_0}{dt}=\frac{kAS}{2\mu\Delta x\,V_{u,\mathrm{eff}}}
\left(U_1^2-U_0^2\right)
$$

$$
\frac{dU_N}{dt}=\frac{kAS}{2\mu\Delta x\,V_{d,\mathrm{eff}}}
\left(U_{N-1}^2-U_N^2\right)
$$

BDF integrates these equations using an analytic sparse tridiagonal Jacobian.
Its fixed tolerances are rtol=1e-8 and absolute tolerance 0.01 Pa/S for U.
The relative tolerance applies to shifted pressure P+b. Metadata records the
engine version and tolerances. The explicit timestep safety factor is disabled
for BDF, as it has no role in that integration method.

The reference Euler solution has finite timestep error. BDF need not reproduce
its default-timestep curve exactly: reducing the reference safety factor makes
the reference converge toward the BDF solution at the same spatial grid.
Their fitted k values can therefore differ slightly at a fixed grid.

The continuous spatial equations conserve the weighted discrete pressure-volume
inventory. BDF preserves that linear invariant to numerical precision in the
checks; explicit stepping in transformed pressure introduces inventory drift.
Both retain the reference's finite-grid initial half-cell storage contribution.
Accurate time integration does not eliminate the need for spatial convergence.

## Inversion and resource controls

At fixed b and other physical inputs, k multiplies the spatial ODE right-hand
side. The existing permeability/time scaling therefore applies. Cached BDF
curves use its dense interpolant, rather than linearly interpolating widely
spaced adaptive steps. A new forward solve at the final k checks that cache.
Measured sample times and both pressure residuals remain unchanged.

With BDF selected, the budget control reads **Maximum BDF RHS evaluations**.
With Reference explicit selected, it reads **Maximum FDM steps per solve**.
The saved `max_steps` field supplies that backend-specific work limit. BDF
cancellation is checked inside RHS/Jacobian evaluations, so it need not wait
for a long explicit integration to finish. Invalid states, failed convergence
and exhausted budgets produce errors rather than accepted results.

Results/export include the selected backend, adaptive accepted-step count,
RHS/Jacobian/LU counts, tolerances, grid and inventory diagnostics. `dt_s` is
null for BDF because its timestep varies. Original-format explicit records
retain a numeric dt.

This change does not resolve uncertainty in porosity, initial core pressure,
valve topology, gas EOS or temperature. The model remains isothermal ideal-gas
flow with fixed b; a faster fit cannot establish those experimental assumptions.

## Verification

Tests cover uniform pressure, zero k, reference time convergence at nonzero slip
and low-porosity near-vacuum conditions, known-k inversion from original-solver
synthetics, inventory, dense-output bounds, cancellation/work budgets and GUI
selection/persistence. See the release validation report for timings and scope.

SciPy method documentation: https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html
