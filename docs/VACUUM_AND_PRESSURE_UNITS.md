# Vacuum and pressure display

## Evacuated core and downstream storage

When the core, downstream chamber DD and downstream tank VD are evacuated and
then equilibrated together, all start at the measured initial downstream
pressure Pdi. If upstream dead volume DU remains connected to the core during
evacuation, it also starts at Pdi: select **Isolated tank**, enter/load DU, and
choose **Downstream initial** for DU's pressure source. Only VU starts at the
pre-opening charged-tank plateau Pui.

Use each run's own stabilized initial and final plateaus. Evacuating before
each pulse helps reproduce initial conditions; it does not establish exactly
zero absolute pressure or guarantee identical apparent porosity across runs.

## Gauge pressure can be negative

The conversion is:

    Pabsolute = Pgauge + measured local Patmospheric + sensor correction

For example, -12 psig with measured atmospheric pressure 13 psia is +1 psia.
These are illustrative values, not an atmospheric-pressure recommendation.
For an absolute recording, atmospheric pressure is not added.

The solver and gas-balance EOS require positive absolute pressures. A converted
reading at or below zero indicates an incompatible basis, source-unit mapping,
reference pressure or sensor calibration. Evacuation is not a reason to permit
negative absolute pressure. Near vacuum, sensor-zero errors can dominate the
small remaining absolute pressure. Check against an independent absolute/vacuum
reference; do not assume perfect vacuum from the gauge display alone.

The analysis window exposes the same **Recording basis (session)** and
**Measured atmospheric P** used by the Pulse picker. They affect all pulses in
that session. Enter the measured local atmospheric pressure; do not substitute
a larger value simply to make the calculation pass.

## Sensor corrections

Upstream and downstream **sensor correction** inputs are additive, with positive
values increasing measured pressure. Apply a value only when supported by an
independent calibration/reference. An offset cannot be inferred merely from
the fact that the core was evacuated. Correcting only an initial plateau while
leaving the same sensor's final plateau/transient unchanged is inconsistent.

The application therefore applies each correction to that pressure channel's
initial mean, final mean and entire transient. Raw recording samples and their
original statistics are preserved. Corrections are saved in AnalysisSettings
in Pa, included in result fingerprints, and reported in analysis warnings.
Changing them requires recalculating porosity and permeability. Source
capture/calibration plots remain the uncorrected measurements; analysis plots
show corrected absolute pressures. These per-pulse corrections do not silently
modify the separate check-plug volume calibration.

## Visible previews and separate state calculations

An invalid converted absolute pressure no longer blanks the measured analysis
preview. Its warning reports the channel/state, the first offending transient
sample's valve-relative time when applicable, and its value in kPa absolute and
psia. Data are not clipped, shifted automatically, or silently excluded.

Late-time/FDM analysis still rejects invalid absolute samples or initial/final
state means. Boyle porosity uses the initial/final state means only: a bad
transient sample outside those state inputs does not invalidate the separate
gas-balance calculation. Boyle still rejects invalid state means.

## Psi versus kPa

Select **Pressure display → psi** in the main toolbar, or the corresponding
selector in the permeability, porosity or Klinkenberg dialog. The shared choice
updates capture/calibration pressure curves and statistics, atmospheric and
receiving pressure inputs, drift limits, analysis noise/floor/slip/correction
inputs, model/residual curves and reports. Absolute report values use **psia**;
capture plots retain their explicit Gauge/Absolute label. Pressure differences
and slip b use psi without adding atmospheric pressure.

The Klinkenberg plot uses reciprocal pressure in psi^-1 or kPa^-1, consistently
rescaling its line. Display changes preserve numerical results and fingerprints;
pressure inputs retain exact canonical values behind display rounding. The
choice lasts for the current application run. JSON/Excel exports keep their
existing explicit canonical/original units rather than relabelling stored data.

Analysis fingerprints include sensor-correction inputs. Changing these inputs requires recalculation of the corresponding results. The FDM forward solver is unchanged.
