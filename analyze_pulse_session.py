"""Reproduce selected-pulse analysis without opening Qt.

Example: python analyze_pulse_session.py examples/synthetic_fdm_demo.json result.json
Use --settings inputs.json to supply an AnalysisSettings dictionary in SI units.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
from pulse_selector.pulse_capture_io import load_pulse_session, protect
from pulse_selector.permeability import AnalysisSettings, extract_trace, late_time, history_match, analysis_fingerprint, _finite_list


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--pulse',help='Exact captured pulse label or UID; defaults to first pulse')
    parser.add_argument('--settings',type=Path,help='SI-unit AnalysisSettings JSON; otherwise use saved settings')
    parser.add_argument('--method',choices=['late','fdm','both'],default='both')
    args=parser.parse_args()
    project=load_pulse_session(args.session)
    pulses=[p for p in project.pulses if args.pulse is None or args.pulse in (p.uid,p.label)]
    if not pulses:
        parser.error('No matching captured pulse.')
    pulse=pulses[0]
    raw=json.loads(args.settings.read_text()) if args.settings else project.analysis_settings.get(pulse.uid)
    if raw is None:
        parser.error('No saved analysis settings: supply --settings or configure this pulse in the GUI first.')
    if args.output.resolve()==args.session.resolve():
        parser.error('Choose a separate output; the source session cannot be overwritten.')
    protect(project,args.output)
    settings=AnalysisSettings(**raw)
    trace=extract_trace(project,pulse,settings)
    results={}
    if args.method in ('late','both'):
        results['late']=late_time(trace,settings)
    if args.method in ('fdm','both'):
        results['fdm']=history_match(trace,settings,progress=print)
    payload=dict(format='pulse-permeability-result',schema_version=1,pulse_uid=pulse.uid,pulse_label=pulse.label,
        sample_id=pulse.sample_id,dataset_id=pulse.dataset_id,settings=asdict(settings),
        fingerprint=analysis_fingerprint(project,pulse,settings),results=results,
        measured=dict(time_s=trace.time_s.tolist(),upstream_pa=_finite_list(trace.upstream_pa),
                      downstream_pa=_finite_list(trace.downstream_pa)))
    descriptor,name=tempfile.mkstemp(dir=args.output.parent,suffix='.tmp')
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(payload,stream,indent=2,allow_nan=False)
        os.replace(name,args.output)
    finally:
        Path(name).unlink(missing_ok=True)
    print(args.output)


if __name__=='__main__':main()
