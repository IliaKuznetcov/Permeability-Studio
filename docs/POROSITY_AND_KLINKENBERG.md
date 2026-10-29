# Porosity and Klinkenberg workflow

## Overview

Porosity is estimated separately from the permeability fitting workflow and must be explicitly applied to subsequent fits. A sample-level regression combines selected pulse results for Klinkenberg analysis. Previously saved fitting results are not silently overwritten.

## Estimate porosity from a captured pulse

1. Open the pulse analysis window and check the pressure channels, measured
   dimensions, pressure basis and barometric pressure. Use the actual measured
   atmospheric pressure for gauge recordings.
2. Link the calibrated volumes and review the valve topology. Connected volumes
   are Vu=VU+DU and Vd=VD+DD, excluding core pores. The volume calibration must
   describe the same boundaries used during the actual pulse.
   Choose **Isolated tank** if the valve isolated charged VU from DU before
   opening. **Use linked calibration** also fills DU separately; charged VU is
   the connected upstream volume minus DU. Select DU's initial pressure source:
   **Downstream initial** if DU was equilibrated with that side, or a known
   **Entered absolute pressure**. DU uses the selected core/proxy temperature.
   Older sessions retain **Combined storage**, meaning all VU+DU was precharged;
   review and change this explicitly for the isolated-tank apparatus.
3. Click **Boyle porosity…**. Confirm that the core was initially equilibrated
   with the downstream side, and that the final windows represent equilibrium
   in a closed system. If the core was at another pressure or had a nonuniform
   initial pressure profile, do not use this estimator's standard initial condition.
4. Choose **Common temperature** for an isothermal balance, or **Captured
   temperatures** to use the separate initial/final state means. Map upstream,
   downstream and core temperature. A downstream sensor can be selected explicitly
   as a core-temperature proxy; that assumption is reported.
5. Choose ideal gas (Z=1), or optionally Peng–Robinson nitrogen when nitrogen is
   the actual gas and appropriate temperatures are known. The nitrogen EOS reuses
   the existing calibration implementation. It does not change the FDM physics.
6. Review the final sensor-mismatch and drift limits. Calculation is rejected if
   the selected final pressures disagree beyond the entered mismatch limit,
   drift beyond the entered limit, or imply nonphysical porosity. Initial pressure
   drift and temperature drift are reported for review.
7. Calculate and inspect pore volume and porosity. **Use this porosity in analysis**
   changes the model input explicitly. Recalculate late-time/FDM fits afterward.
   The entered porosity is not used to obtain the gas-balance estimate.
8. Save pulses to persist the estimate/options, or export it as JSON. Result
   fingerprints are independent of the entered porosity and fitting windows,
   but depend on the recording, states, channels, geometry, volumes and gas-law inputs.

The output is connected, gas-accessible porosity under the measurement conditions.
It need not equal total porosity. Leakage, adsorption, gas reaction, sample
compression, unaccounted connected volume, sensor offsets or failure to reach
core equilibrium can bias it. Passing final pressure/drift checks alone does
not establish internal core equilibrium.

### Select stabilized states, not the valve transient

Use the mean of a stable upstream-tank plateau immediately before valve opening
as Pui, and the corresponding initial downstream plateau as Pdi. Pf is a separate
final, equalized pressure, obtained from the two accepted final windows. The final
plateau does not determine the initial tank pressure. Do not use the first
post-opening drop, a transient average, or the initial differential pressure in
place of Pui. Pressure and temperature windows must represent the same states.
The complete transient remains available for history matching.

### Isothermal Boyle balance: isolated tank

VU alone starts at Pui; DU, VD+DD and the core start at Pdi when DU's source is
**Downstream initial**. All volumes reach Pf at a common unchanged temperature:

$$
V_U P_{u,i}+(D_U+V_D+D_D+V_p)P_{d,i}
=(V_U+D_U+V_D+D_D+V_p)P_f
$$

$$
V_p=\frac{V_U(P_{u,i}-P_f)+(D_U+V_D+D_D)(P_{d,i}-P_f)}{P_f-P_{d,i}}
$$

For a separately entered initial DU pressure Pdui, replace the DU contribution
in the numerator with $D_U(P_{du,i}-P_f)$. Retaining the charged tank pressure while
assigning all VU+DU to it overstates initial inventory when DU started lower.
Changing this assumption need not produce any particular expected porosity;
review the measured states and physical setup rather than target a value.

### Isothermal Boyle balance: combined storage

Let Vu and Vd be the connected non-core volumes, Vp the core pore volume, and
Pui/Pdi the initial tank pressures. The standard initial core pressure is Pdi.
All compartments reach Pf at the same unchanged temperature:

$$
P_{u,i}V_u+P_{d,i}(V_d+V_p)=P_f(V_u+V_d+V_p)
$$

Therefore:

$$
V_p=\frac{V_u(P_{u,i}-P_f)+V_d(P_{d,i}-P_f)}{P_f-P_{d,i}}
$$

$$
V_{\mathrm{bulk}}=\frac{\pi D^2L}{4},\qquad
\varepsilon=\frac{V_p}{V_{\mathrm{bulk}}}
$$

The calculation uses absolute pressures consistently. A common atmospheric offset
cancels in this particular isothermal ideal-gas expression; it does not generally
cancel when temperatures or real-gas Z factors differ. Absolute pressure remains
required for the gas model and permeability analysis.

### Temperature and real-gas correction

For each compartment and state, define $C=P/(ZT)$, with $T$ in kelvin. Then

**Isolated tank:**

$$
V_p=\frac{V_U(C_{u,i}-C_{u,f})+D_U(C_{du,i}-C_{du,f})+V_d(C_{d,i}-C_{d,f})}{C_{c,f}-C_{c,i}}
$$

**Combined storage:**

$$
V_p=\frac{V_u(C_{u,i}-C_{u,f})+V_d(C_{d,i}-C_{d,f})}{C_{c,f}-C_{c,i}}
$$

Initial core pressure is Pdi; final pressure is the mean of the two final pressure
windows after their difference passes the user's tolerance. Each compartment may
have a different initial/final temperature. The state means are approximations to
representative stabilized conditions; pressure and temperature windows should
refer to the same states.

DU uses the core/proxy sensor for both initial and final temperature; its Z is
computed at its own pressure and that temperature. The report/export lists each
state's pressure, temperature and Z, plus charged volume and DU, for review.

### FDM initialization after opening

The reference forward solver is unchanged. For the isolated-tank setting its
adapter approximates instantaneous ideal-gas, isothermal VU–DU mixing without
core flow during this short mixing step:

$$
P_u(0^+)=\frac{V_U P_{u,i}+D_U P_{du,i}}{V_U+D_U}
$$

It then uses VU+DU as upstream storage, VD+DD as downstream storage, and a core
initially at Pdi. This preserves the continuous ideal/isothermal inventory of
the separate external volumes; the existing solver's finite-grid storage bias
and numerical inventory drift remain. Both the raw pre-opening plateau and
mixed FDM pressure are reported. Exclude valve artifacts from fitting, but
retain the actual opening time as simulation t=0. A delayed fit does not restart
the model. No dynamic valve, thermal mixing or real-gas FDM was added.

A real-gas or temperature-corrected porosity estimate does **not** turn the current
isothermal ideal-gas FDM into a thermal/real-gas model. Compare assumptions carefully.
No propagated uncertainty estimate is supplied yet. Estimating porosity to enforce
gas inventory is not independent validation of those inventory assumptions.

## Klinkenberg across several pulses

The relation is

$$
k_{\mathrm{app}}(P_m)=k_{\infty}\left(1+\frac{b}{P_m}\right)
=k_{\infty}+(k_{\infty}b)\frac{1}{P_m}
$$

Plot apparent permeability against reciprocal **mean absolute pressure**. The
intercept is intrinsic permeability; slope/intercept is b, using consistent units.
The app plots $x=1/P_m$ in $\mathrm{MPa}^{-1}$ and $y$ in microdarcy, then reports $b$ in kPa.
Pm is the mean pressure in each selected late-time interval, not simply initial
upstream pressure and not the initial pressure difference.

1. Capture/import the pulses into one pulse session; snapshots can come from
   several source recordings. Give the captured records the same nonempty
   **Sample ID** and save any edits before analysis.
2. Compare the per-pulse porosity estimates. Choose a common reviewed porosity for
   the same sample rather than automatically treating their scatter as real
   changes in porosity. Use common geometry, connected volumes and viscosity for
   the selected late-time fits. Real changes in stress/temperature require separate
   interpretation, not forced equality.
3. Calculate and save a late-time fit for every candidate pulse. Different fitting
   windows are allowed, but they must each represent a valid late-time regime.
4. Click **Klinkenberg…**. The table shows all pulses for that Sample ID. Missing,
   stale or low-R² (<0.98) fits cannot be included. Select at least three valid
   points with at least 10% span of mean pressures. More points and a useful
   reciprocal-pressure range provide a better basis for assessment than a bare minimum.
5. Review each original fit's warnings. Confirm the same gas and comparable
   temperature/effective stress. At fixed confining pressure, changing pore
   pressure changes effective stress, which can mimic a pressure-dependent
   permeability trend.
6. Fit and inspect the line, intercept, b and scatter. Negative b or nonpositive
   intrinsic permeability is rejected. This is unweighted regression; it does not
   propagate porosity/calibration uncertainties or provide a validated uncertainty interval.
7. Save pulses to retain the sample regression, or export it. **Use b for this FDM
   fit** applies b only as the fixed input for the currently selected pulse.
   Run that FDM fit again. This is not a joint nonlinear fit of all pulses.

Saved sample regressions are retained; opening the
review window requires recomputation before they can be applied or exported.
The saved point list contains the pulse UIDs, source-fit fingerprints and inputs.
If you change porosity or other sample properties, recompute the individual
late-time fits before making a new correction.

## Can one pulse determine both intrinsic k and b?

One scalar late-time result gives a single (Pm,k_app) point and cannot separately
identify two free parameters. A full nonlinear transient can sometimes provide
independent sensitivity to k and b when mean pressure changes sufficiently and
the experiment has suitable sensitivity. It is not categorically impossible.

For similar upstream/downstream tank volumes, mean-pressure contrast can be too
small and k/b sensitivity can be strongly correlated. The current single-pulse FDM
fit deliberately keeps b fixed. Several pulses at different mean pressures on the
same core are the practical workflow implemented here. Joint nonlinear inversion
and sensitivity/uncertainty analysis remain separate future work.

Primary references:

- Jannot et al., SCA2007-08, *A Detailed Analysis of Permeability and Klinkenberg
  Coefficient Estimation from Unsteady-State Pulse-Decay or Draw-Down Experiments*:
  https://www.jgmaas.com/SCA/2007/SCA2007_08.pdf
- Wang et al. (2021), discussion of mass-conservation/Boyle porosity in conventional
  closed pulse-decay experiments: https://doi.org/10.1002/ese3.989
- University of Kansas TORP pulse-decay apparatus:
  https://torp.ku.edu/pulse-decay

## Reproducible demonstrations and tests

Open `examples/synthetic_klinkenberg_demo.json` in Pulse picker, select any row,
open analysis → Klinkenberg, confirm the synthetic conditions and fit the three
points. The reference FDM generated them with intrinsic k=3e-17 m² and b=200 kPa.
Expected regression: approximately k∞=30.4002 µD and b=200.0011 kPa. The small
bias reflects the finite-grid numerical/late-time approximation. These are
synthetic measurements, not experimental validation.

Recreate with `python examples/create_klinkenberg_demo.py`.

The porosity tests independently construct gas inventories with known phi=0.237
and recover that value under isothermal, varying-temperature and PR-nitrogen
conditions. UI tests verify confirmation, applying phi/b, changed-input handling,
point exclusion, common-input checks, plots and session round trips.

Modules: `porosity.py`, `porosity_view.py`, `klinkenberg_view.py`. The reference
`fdm_solver.py` remains unchanged.
