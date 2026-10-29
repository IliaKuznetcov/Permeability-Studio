# Pulse picker guide

For late-time methods, FDM fitting, and stored results, see [PERMEABILITY_GUIDE.md](PERMEABILITY_GUIDE.md).

Use this tab for actual rock-plug pulse selection. Check-plug ROI and Volume calculation remain separate calibration workflows.

## One pulse, step by step

1. Open a raw XLSX/CSV log. Select its shared time columns and all measurement channels needed later: upstream pressure, downstream pressure, and available tank temperatures. Verify units and explicitly assign upstream/downstream locations, particularly for generic names such as P2/P8.
2. Open **Pulse picker**. Pressure channels appear together in the upper plot in kPa; temperature channels appear below in °C. All original samples and units are retained for export.
3. Choose **Valve opening** in the Click/drag list and click at the observed valve event, or enter its elapsed time in **Valve opening (t=0)**. Dragging/typing that marker disables automatic midpoint suggestion. The dashed orange line marks a time reference; it does not select averaging samples.
4. Click **Place regions around valve** for an initial placement, or select Initial pressure/Final pressure and position their regions by clicking, dragging the band/edges, or typing numeric Start/End values. Blue is the initial state; green is the final state after that same pulse. Avoid subsequent pulses, venting and temperature transients when choosing final-state averages.
5. With **Link temperature to pressure regions** checked, temperature state windows follow the corresponding pressure windows. Uncheck it to move initial/final temperature windows independently. Those temperature windows apply to all selected temperature channels; channel identities remain separate in the statistics.
6. Review all initial/final means, sample SD, finite n and drift slopes on the right. Hover over a value for min/max, actual included sample times, coverage, maximum gap and source rows. These diagnostics describe the chosen interval; they do not automatically prove equilibrium.
7. Enter a unique Pulse ID and optional Sample ID/Notes. For extraction alone, measured barometric pressure and calibrated volume references can remain unset. Set the correct **project-wide** pressure basis and measured barometric value before preparing absolute-pressure inputs for later modeling.
8. By default the full transient runs from the exact valve marker through the latest final-state ROI end. Optionally check **Choose separate fitting interval** and enter a fitting subinterval; its purple band is shown on the plots. The full transient is retained independently. This supports later exclusion of valve transients or late-time comparison without discarding raw data.
9. Choose the context duration before the valve. Exported context also includes the complete initial state windows even if they begin earlier than that duration.
10. Click **Capture pulse**. The record appears in the pulse collection. It stores both states, exact valve time, full transient and fitting interval. The form starts a new pulse ID but does not automatically guess the next experimental pulse.
11. Open the next recording, or move the windows for the next pulse in this recording. Already captured pulses remain in the collection across files.
12. Select a captured row and click **Edit selected** (or double-click it) to reopen its original recording and exact windows. Click **Save edits to pulse** to commit changes to that record. **Remove pulse** and **Undo removal** operate on captured records.
13. Use **Save pulses** to preserve the editable session and embedded recording samples. Use **Export pulses** for the Excel output. Uncaptured form edits are drafts; capture or save edits before saving a session.

## Marker suggestion and chronology

**Suggest marker between pressure regions** places the marker halfway between the initial pressure ROI end and final pressure ROI start as those windows move. It is a convenience, not automatic detection of valve opening. Once you position the marker manually, the checkbox turns off and later ROI edits leave your selected valve time unchanged. Re-enable the checkbox if you want a new midpoint suggestion.

Initial pressure and temperature ROIs must end before the selected valve time. Final pressure and temperature ROIs must start after it. Capture errors show the actual conflicting times. An ROI needs at least three finite readings in each selected channel. If a selected sensor has no usable readings in that interval, adjust the interval or reopen the log with the appropriate channels; missing values are not replaced with zeros.

A separate fitting interval must begin at/after the valve event, end at/before the latest final-state ROI end and include at least three time samples.

## Time zero and temperature inputs

The exported time reference is `sample elapsed time - selected valve elapsed time` for every channel. A valve at 50.25 s followed by the first sample at 51 s produces a first transient time of 0.75 s. The application neither interpolates a fabricated point at zero nor shifts the first sample to zero. Initial/context samples have negative relative times.

Before/after temperature values are the separate Tu/Td tank means. No single pulse-average temperature is substituted. Unlike the check-plug expansion balance, this extraction stage does not require the initial receiving-cavity Tb field. The eventual permeability model must explicitly define its initial rock/tank temperatures and assumptions from the verified FDM formulation.

## Copying a calibrated volume reference

Open the saved calibration session in **Volume calculation** and review its current fitted inputs. In Pulse picker, click **Use current calibrated volumes**. The project stores VU, VD, DU and DD plus source/run IDs, EOS, fit diagnostics and input conditions. It does not run a permeability fit or overwrite the calibration collection.

Volume references are copied at that time. If you later edit the calibration, press the button again to update the pulse reference. The measured barometric pressure for the pulse experiment is a separate project input; it is not silently taken from the calibration experiment.

Use VU+DU and VD+DD for their respective external storage when the eventual verified model requires that convention. Keep pore storage separate. Review provisional calibration assumptions before reporting final permeability values.

## Pulse-session and calibration-session files

- **Save pulses/Open pulses** handles `paired-pulse-session` JSON files, including all selected raw recording traces and paired-state statistics. Statistics are recomputed on reopening.
- **Volume calculation → Save/Open session** handles the existing calibration JSON files.
- Opening either type preserves the other collection. Starting a new pulse session does not erase calibration runs; starting a new calibration session does not erase pulses.
- Both collections prompt about unsaved captured changes when the application closes.
- Original source XLSX/CSV files are protected from overwrite by session/export operations.

## Excel sheets

| Sheet | Contents |
|---|---|
| `pulse_decay` | Full transient from valve opening through the latest final-state end, in original units. Grouped columns per pulse. |
| `pulse_raw_context` | Full initial-state/context samples plus transient through the final-state end, in original units. |
| `pulse_fit_window` | Independently selected fitting subset, referenced to the same valve zero. |
| `pulse_states` | Initial/final statistics for every channel in canonical kPa/°C, units/location, actual sample bounds, relative times and source-row references. Optional absolute-pressure mean column is blank for gauge data without a measured barometric input. |
| `state_samples` | Exact original-unit samples that enter each initial/final statistic, including gaps. |
| `selection_metadata` | IDs, source hash, requested windows, valve reference, fitting/context indices, notes and review messages. |
| `channel_metadata` | Source channel mappings, units, locations and import diagnostics. |
| `analysis_setup` | Project pressure basis/barometric input and copied calibrated-volume reference/provenance. |

## Try the new pulse demo

Use **Pulse picker → Open pulses** to open `examples/synthetic_pulse_demo.json`. It contains one clearly labelled synthetic pulse with both pressure channels, before/after windows and valve t0=300 s. It is for manual-control/export practice and has no assigned or fitted permeability. It is distinct from `examples/synthetic_demo.json`, which is the existing known-volume calibration demonstration.

The captured pulse data can be analyzed using the permeability tools described in [PERMEABILITY_GUIDE.md](PERMEABILITY_GUIDE.md).
