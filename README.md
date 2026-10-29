# Permeability Studio

**Version 1.0.0 — first public source release**

Permeability Studio is a Python/PySide6 desktop application for processing gas pulse-decay experiments, calibrating gas-accessible volumes, and estimating permeability and porosity. The source release is based on an internally developed and tested application previously designated 4.5.2; the public version number starts at 1.0.0. Historical numerical methods and saved-session schemas are preserved.

## Features

- Import pressure/temperature recordings and interactively select valve-opening times and stabilized initial/final regions.
- Estimate upstream, downstream, and dead volumes from check-plug expansion measurements.
- Compute pulse-decay permeability with late-time analysis and finite-difference forward/history-matching models (reference explicit and adaptive BDF integration).
- Estimate gas-accessible porosity from gas balance and review multiple pulses with Klinkenberg regression.
- Inspect plots, model fits, and export analysis/session results.

## Requirements

Windows (or a supported platform with PySide6) and Python with the dependencies in `requirements.txt`.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python permeability_studio.py
```

Alternative launcher: `python pulse_log_selector.py`.

## Windows executable

Run `build_windows.bat` on a Windows computer with Python installed to produce `dist\permeability_studio.exe` (single-file distribution). The script creates a separate `.venv-build` environment. Alternatively see [Windows build instructions](docs/WINDOWS_APP.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Pulse picker](docs/PULSE_PICKER_GUIDE.md)
- [Calibration](docs/CALIBRATION_GUIDE.md)
- [Permeability analysis](docs/PERMEABILITY_GUIDE.md)
- [Porosity and Klinkenberg regression](docs/POROSITY_AND_KLINKENBERG.md)
- [Adaptive FDM](docs/ADAPTIVE_FDM.md)
- [Pressure and vacuum units](docs/VACUUM_AND_PRESSURE_UNITS.md)
- [Shale Sample 1 reference inputs and dimensions](docs/SHALE_SAMPLE_1_INPUTS.md)

## Scientific scope

Review the assumptions, pressure basis, calibration topology, convergence, and fit warnings before interpreting experimental results. The baseline pulse-decay treatment includes assumptions about isothermal gas flow, calibrated boundary storage, and constitutive transport laws; model outputs are not independent experimental validation.

## Data and licensing

The `examples/` directory includes anonymized shale pulse-decay and check-plug calibration sessions with recorded time-series measurements and saved analysis results. Local source paths, sample labels, and source-workbook hashes have been anonymized; measurement values are retained. The public example is not synthetic. Stored analysis results may need to be recalculated after editing metadata, because fit fingerprints can depend on pulse labels. Confirm authorization before distributing research data or intellectual property. No software license is supplied in this archive; choose and add a license only after verifying publishing rights.

## Example sessions

- `examples/pulse_session.json`: recorded shale-sample pulse-decay data with captured pulses and analysis results. Open through the application session loader.
- `examples/check_plug_session.json`: check-plug volume-calibration session. Open from the calibration tab.

The example datasets are anonymized for source-path and sample-label disclosure; they retain original measured values and acquisition timestamps. [Reference chamber volumes and plug dimensions](docs/SHALE_SAMPLE_1_INPUTS.md) are provided separately for testing; these differ from values stored in the original session and have not replaced them.
