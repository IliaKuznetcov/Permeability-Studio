# Manual ROI extraction and check-plug volume calculation

## The measured states

For an upstream expansion the solver takes initial/final tank temperatures from Tu. For a downstream expansion it takes them from Td. The thermocouples are inside their corresponding tanks.

| Stored statistic | Meaning |
|---|---|
| Pi, Pf | Initial/final pressure means, normalized to kPa but still in the selected original gauge/absolute basis in the ROI display. |
| Tu_i, Tu_f | Upstream tank temperature means from the selected initial/final Tu windows, in °C. |
| Td_i, Td_f | Downstream tank temperature means from the selected initial/final Td windows, in °C. |
| Tb | Separately entered initial temperature of the receiving dead/cavity volume, in °C. |

The opposite tank remains isolated during these calibration topologies. Its thermocouple does not directly measure Tb. Enter Tb with its own measurement source or explicitly documented equilibrium assumption. The solver uses the charged tank's final temperature as the common connected final temperature only after you check **Use tank Tf as common final T**. If the connected volumes are not sufficiently represented by one final temperature, this common-temperature model needs a physical extension; averaging unrelated tank sensors does not implement that extension.

Pressure/temperature means should normally describe the same stabilized state. The **Match temperature windows to pressure** button copies the initial/final pressure windows to both temperature channels. You may override each temperature ROI explicitly, for example to investigate sensor response, but mismatched time windows are flagged and exported.

## Selecting windows

There are six possible ROIs. One source pressure channel supplies Pi and Pf; the two temperature channel assignments supply Tu_i/Tu_f and Td_i/Td_f. Assign the sensors explicitly. Source-column metadata and original units remain attached to each ROI.

- Select a target from the Click/drag ROI list. That ROI becomes draggable on its plot. Other ROIs remain visible and cannot intercept that drag.
- Click in the appropriate plot to center a window of the chosen Click width. Pressure targets use the upper plot; temperature targets use the lower plot.
- Drag the active band's middle or either boundary, or type its Start/End in the table. Plot zoom/pan uses pyqtgraph's normal controls.
- Move the dashed line to the expansion time. Initial windows must end before it; final windows must begin after it. Each selected window must contain at least three finite readings in its channel.
- **New run** starts a new record ID while retaining useful setup and window positions. It does not save or overwrite a run. **Capture new run** appends a new record; IDs must be unique.
- To revise a saved record, select it in the Volume calculation table and click **Edit selected run**, or double-click its row. Its original recording and exact requested windows reopen, even if another log is currently open in the original picker. Click **Save edits to run** to replace that record's statistics and inputs.
- Removing a run is separate from excluding it. **Undo removal** restores the last removed record. Exclusion keeps its data and requires a reason for fitting or saving a session.

The six default intervals are not automatic plateau detection. Particularly for the supplied hollow-plug files, the end of the recording includes venting after several expansions. Choose the intended final plateau before that event.

## Statistics and drift

The ROI mean is the arithmetic mean of finite samples, not a time-weighted mean. SD is sample standard deviation with ddof=1. A mean/SD/count, minimum, maximum, duration, source-row bounds, finite-reading coverage and maximum gap are retained. Missing readings are ignored within that channel without deleting another channel's data.

Slope is the ordinary least-squares linear slope versus the actual elapsed times, reported in kPa/min or °C/min. A small slope alone does not prove stationarity or absence of leaks. Inspect transients, scatter and the full trace. Reported sample SD is not a complete uncertainty estimate and serial samples need not be independent.

Pressure and temperature drift limits start **Off**. Enter positive limits based on your instrument resolution, experimental protocol and accuracy needs. Limit exceedances create review notes. They do not silently exclude a run. If a limit is off, the application reports that acceptance limits have not been fully configured. Hover over statistics for more detail; exported `roi_statistics` contains all fields.

## Gas-law inputs and units

- All solver pressures are kPa absolute; all solver temperatures are K. Source samples remain in their original units in the raw-data sheets/session.
- For Gauge inputs, add the entered measured barometric pressure once to Pi and Pf. For Absolute inputs, use them directly.
- Vented receiving mode sets Pb to the measured barometric pressure, already absolute. Measured absolute mode uses the separate Pb field, including an explicitly measured evacuated state. No second barometric addition is applied to Pb.
- No barometric value, Tb value, or Tb source is silently supplied. Missing values allow ROI capture but block use of that included record in fitting.
- The final-temperature confirmation and a positive known Vp for through-bore runs are also required for calculation readiness. A shared pressure/temperature state assumption remains the operator's responsibility.
- Default Zb is 1 for the initial receiving gas, as in the supplied manual. Record an appropriate Zb if that approximation does not apply.
- Optional Zi/Zf overrides supersede the selected EOS for that record and are retained in sessions/metadata. An override of zero is not accepted; zero in the UI means automatic EOS calculation.

The gas model choices are nitrogen Peng–Robinson or ideal gas (Z=1). The PR calculation uses Tc=126.192 K, Pc=3395.800445 kPa and omega=0.0372, with the manual's coefficients. It solves the cubic and selects the largest physical real gas root, rather than using a fixed number of Newton steps. This is not a general gas-mixture property model. Following the manual, the final nitrogen/initial-air mixture is approximated with the nitrogen Z factor and flagged accordingly.

## Plug volume and topology

For a straight through-bore with ID and gas-accessible length entered in mm:

$$
V_p\,[\mathrm{cm}^3]=\frac{\pi\,d_i^2\,L_p}{4000}\quad (d_i,L_p\text{ in mm})
$$

A more accurate measured/certified gas volume may be entered directly. Outer diameter does not enter this bore-volume formula. Geometry input boxes are a calculator convenience; the numeric Vp field is the authoritative value retained for the run. Record detailed geometry provenance in Notes when needed.

| Configuration | Receiving volume |
|---|---|
| Through-bore | DU + DD + Vp |
| Solid isolating | DU for upstream charging; DD for downstream charging; Vp=0. |
| Side-only cavity | DU + Vp for upstream charging; DD + Vp for downstream charging. |

VU/VD are the isolated tank volumes. DU/DD are bounded by the plug faces and the corresponding closed tank-isolation valves. The opposite tank is excluded from the expansion. Correct valve topology and receiving-volume reset are experimental inputs, not something inferred from the pressure trace. Record relevant valve states in Notes. Changing the physical volume boundaries requires a consistent recalibration.

## Fitting

For each run:

$$
A=\frac{P_i}{Z_iT_i},\qquad B=\frac{P_b}{Z_bT_b},\qquad C=\frac{P_f}{Z_fT_f}
$$

The balance is:

$$
(A-C)V_{\mathrm{tank}}+(B-C)V_{\mathrm{receive}}=0
$$

With parameter order $[V_U,V_D,D_U,D_D]$:

| Run | Design row | Right-hand side |
|---|---|---|
| Upstream through-bore | $[A-C,\ 0,\ B-C,\ B-C]$ | $(C-B)V_p$ |
| Downstream through-bore | $[0,\ A-C,\ B-C,\ B-C]$ | $(C-B)V_p$ |
| Upstream solid/side-only | $[A-C,\ 0,\ B-C,\ 0]$ | $(C-B)V_p$ |
| Downstream solid/side-only | $[0,\ A-C,\ 0,\ B-C]$ | $(C-B)V_p$ |

The solver uses unweighted ordinary least squares via NumPy's SVD-based `lstsq`. At least four independent included rows and rank four are required. Through-bore-only rows cannot distinguish DU from DD. Known nonzero cavity volumes establish the absolute volume scale. More than four independent runs are needed to estimate residual-based SE.

The calculation tab reports volumes, conditional OLS standard errors, approximate normal 95% widths $1.96\,\mathrm{SE}$, rank, condition number, SSE, RMSE, and R². R² and all fitting diagnostics use the same included records. No excluded row contributes a zero-valued RHS to the R² calculation.

$r=X\boldsymbol{\beta}-y$. With kPa and cm3, the independent closure display in mmol is:

$$
n_{\mathrm{final}}-n_{\mathrm{initial}}=-\frac{r}{8.314462618}\quad\text{(mmol, for }r\text{ in kPa cm}^3\text{/K)}
$$

The residual plot follows the included table order. The Excel export includes each design row, RHS, fitted RHS, residual and closure error. The uncertainty is conditional on the design matrix; it does not propagate pressure, temperature, thermocouple placement, barometric, plug-geometry, Z-model, or correlated-sample uncertainty.

Nonpositive fitted volumes and numerical ill-conditioning are reported for review. Values are never silently clipped to be positive. The expected $P_b<P_f<P_i$ ordering is checked as a review note. Numeric readiness and a successful fit do not certify thermal equilibrium, leak tightness, or the correctness of selected valve boundaries.

## Sessions and exports

**Save session** writes a versioned JSON file containing captured records, requested ROI bounds, channel metadata, gas-law inputs, inclusion reasons, EOS choice and embedded cleaned source traces. Source paths, source-row indices and SHA256 of each original file (when available) are retained. Reopening recomputes ROI statistics from the embedded samples; it does not require the old source-file path to be accessible. Requested edges and actual included sample times are both retained.

Capture or Save edits before saving a session: uncommitted form edits/ROIs are drafts, not captured records. Closing/opening a session prompts about unsaved captured calibration changes. Calibration-session saving is separate from paired-pulse saving: use Save pulses in the Pulse picker tab for those records. Source Excel/CSV files are protected against session/export overwrite.

**Export Excel** writes:

| Sheet | Contents |
|---|---|
| `calibration_inputs` | One row per captured run, with explicit absolute-pressure inputs, selected tank Ti/Tf, separate Tb/source, inclusion and readiness/review notes. |
| `roi_statistics` | Every captured channel/window, canonical-unit statistics and original-unit metadata, requested/sample bounds, counts and source rows. |
| `run_metadata` | Source identity, opening time, original pressure basis, receiving mode, limits, final-temperature confirmation and EOS. |
| `roi_samples` | Exact samples within each ROI, in original units, including gaps. |
| `raw_logs` | Full embedded traces in long format, in original units. |
| `source_channels` | Imported channel mappings, original units/locations and import diagnostics. |
| `volume_results` | Available four-volume fit, derived external storage volumes, numerical diagnostics and method notes. |
| `fit_residuals` | Included-row design coefficients, RHS, fitted values, residuals and mole closure. |

Statistics may be exported before the project has enough complete independent runs. In that case `volume_results` explicitly states why no fit is available. Export always recalculates from current captured records, so a stale onscreen fit cannot be exported as current.

The `calibration_inputs` sheet is a new explicit export, not a replacement for the supplied workbook's formula layout. Its pressure values are **absolute**. When transferring values to the old workbook, set that workbook's entry basis to Absolute or convert to its chosen entry basis. Do not paste already-absolute pressures into a workbook configured to add barometric pressure again.

Excel exports are values/statistics snapshots. Save the JSON session for editable ROI reopening. Logs that exceed Excel's row limit can still be retained in JSON; Excel export reports the limit instead of truncating samples.

## Sources and validation boundary

Scientific conventions and topology follow the supplied `dead_volume_calibration_manual(2).pdf`, particularly sections 6–9 and 12. The numerical reference fixture was taken from the supplied 24-included-run `gas_volume_calibration_comprehensive_lower_pressure_with_R^2(3).xlsx`. The manual's worked example has a different inclusion mask; the application never applies either mask automatically to newly captured runs.

The 24-row reference verifies the equation/solver implementation. The supplied experimental summaries contain temperature-window and sensor-assignment concerns identified during review. They were not silently repaired or treated as validated experimental calibration states in the example. Select and approve the actual P/T windows with the correct tank channels before reporting new experimental volumes.
