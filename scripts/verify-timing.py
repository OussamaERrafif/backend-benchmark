"""Real timing repair check; intentionally excluded from the research matrix."""
import json
import sys
from runner import Controller, ROOT, utc
sys.path.insert(0,str(ROOT/'analysis'))
from process_results import stress_windows
import pandas as pd
import argparse
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--existing',type=str);args=parser.parse_args()
controller=Controller('standard')
directory=Path(args.existing) if args.existing else ROOT/'results/raw'/('timing-integration-'+utc().replace(':','-'))/'express/json/window-check'
controller.stop_backends()
try:
    if not args.existing:
        controller.database(reset=True);controller.start('express')
        controller.load('express','json',50,'20s',directory,measured=False,kind='stress')
    summary=json.loads((directory/'k6-summary.json').read_text())
    meta=json.loads((directory/'metadata.json').read_text())
    window=summary['benchmark_window'];requests=summary['metrics']['http_reqs']['values']['count']
    rate=requests/window['duration_seconds']
    assert window['method']=='scenario-monotonic-v2'
    assert 20<=window['duration_seconds']<=21,window
    assert 49<=rate<=51,rate
    stats=pd.read_csv(directory/'docker-stats.csv')
    assert stats.elapsed_seconds.notna().all()
    for _,group in stats.groupby('role'):assert group.elapsed_seconds.diff().dropna().ge(0).all()
    windows=stress_windows(directory)
    observed=sum(item['rps']*item['window_seconds'] for item in windows)
    assert abs(observed-requests)<.01,(observed,requests)
    print(json.dumps({'timing_check':'PASS','requests':requests,'scenario_rps':rate,'original_k6_rps':summary['metrics']['http_reqs']['values']['rate'],'window':window,'stress_windows':windows,'source':str(directory)},indent=2),flush=True)
finally:
    controller.compose('stop','express',check=False)
    controller.database(reset=True)
