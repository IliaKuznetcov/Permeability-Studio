"""Run the same volume calculation without starting Qt.

python calculate_check_plug_volumes.py session.json --output fit.json
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from pulse_selector.calibration import fit_project_runs
from pulse_selector.calibration_io import load_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new JSON result file; otherwise print the result.",
    )
    args = parser.parse_args()
    try:
        project = load_session(args.session)
        fit = fit_project_runs(project.runs, project.eos)
        result = asdict(fit)
        result["units"] = {
            "volumes": "cm3",
            "pressure": "kPa absolute",
            "temperature": "K",
            "residuals": "kPa cm3/K",
            "closure": "mmol",
        }
        encoded = json.dumps(result, indent=2, allow_nan=False)
        if args.output:
            protected = [args.session] + [
                Path(s.source_path) for s in project.snapshots.values() if s.source_path
            ]
            if any(args.output.resolve() == p.resolve() for p in protected):
                raise ValueError(
                    "Choose a new result path; session and source files cannot be overwritten."
                )
            args.output.write_text(encoded + "\n", encoding="utf-8")
            print(f"Saved fit to {args.output}")
        else:
            print(encoded)
    except (ValueError, KeyError, OSError) as exc:
        print(f"Calculation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
