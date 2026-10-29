# Shale Sample 1 — Reference test inputs

These reference values were supplied for reproducing and exploring the shale-sample pulse-decay example in Permeability Studio v1.0.0. Values and units are reproduced as supplied; no recalibration or numerical history matching has been performed using this table.

| Parameter | Value | Unit |
|---|---:|---|
| Barometric pressure | 12.8396 | psi |
| Inlet chamber | 53.54 | cm³ |
| Outlet chamber | 53.73 | cm³ |
| Sample chamber with mandrels together | 24.18 | cm³ |
| Plug diameter | 38.3 | mm |
| Plug length | 50.685 | mm |
| Plug mass | 139.02 | g |

## Using these values

- Treat **12.8396 psi** as the supplied barometric pressure; convert to the units required by the application when entering it. Use the appropriate gauge/absolute pressure convention.
- **Inlet chamber** and **outlet chamber** are the user's reported chamber volumes. The application separately tracks calibration quantities `VU`, `VD`, `DU`, and `DD` (tank and dead volumes). Do not assume the reported chamber volumes map directly to a particular field without confirming the experimental valve topology.
- **24.18 cm³** is the reported *sample chamber with mandrels together*, not the rock's pore volume or automatically the dead volume. Do not use it as porosity or as a tank volume.
- Diameter, length, and mass describe the test plug. Mass is not sufficient by itself to determine porosity without an additional density or pore-volume measurement.

## Important: discrepancy with the saved session

The distributed `examples/pulse_session.json` retains the original saved experimental and calibration inputs, which are **not identical** to this separate table. For example, the session contains `VU = 51.72847731433851 cm³`, `VD = 51.807167131742304 cm³`, `DU = 5.997097309587737 cm³`, `DD = 16.56263518717232 cm³`, and barometric pressure `88.2528933525504 kPa` (roughly `12.80 psi`). These values have deliberately **not** been overwritten by the reference table.

For an independent test using the values above, start a separate analysis or copy of the session and enter consistent calibrated volumes, geometry, and pressure basis. Do not claim the original saved results were computed using the table unless they are recalculated and checked.

The sample has been labeled *Shale Sample 1* for the public example. Its lithology has not been independently verified from laboratory characterization in this release.
