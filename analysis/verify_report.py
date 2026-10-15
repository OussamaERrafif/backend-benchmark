"""Final integrity gate: real coverage and data-bearing charts, never placeholders."""
import json
import re
import pandas as pd
from process_results import ROOT, CONTAINER_FAILURES
from experiment_audit import FRAMEWORKS, SCENARIOS
from investigated_attempts import reviewed_rejection

def verify(require_complete=True, expected_state='COMPLETE'):
    state=json.loads((ROOT/'verification/research-campaign.json').read_text())
    data=pd.read_csv(ROOT/'results/processed/results.csv')
    warmups=pd.read_csv(ROOT/'results/processed/warmup-failures.csv')
    stress=pd.read_csv(ROOT/'results/processed/stress-results.csv')
    errors=[]
    if require_complete and state['state']!=expected_state:errors.append('Campaign is not '+expected_state)
    for profile,sid in state['sessions'].items():
        session=ROOT/'results/raw'/sid
        rows=data[data.session_id==sid]
        if require_complete:
            for framework in FRAMEWORKS:
                for scenario in SCENARIOS:
                    subset=rows[(rows.framework==framework)&(rows.scenario==scenario)]
                    failed=warmups[(warmups.session_id==sid)&(warmups.framework==framework)&(warmups.scenario==scenario)]
                    for repetition in range(1,4):
                        if repetition not in set(subset.repetition)|set(failed.repetition):errors.append(f'{profile}/{framework}/{scenario}: missing repetition {repetition}')
                    for target,level in subset.groupby('target_rps'):
                        actual=set(level.repetition)|set(failed[failed.target_rps==target].repetition)
                        if actual!={1,2,3}:errors.append(f'{profile}/{framework}/{scenario}/{target}: incomplete repetitions')
                    if not (session/framework/scenario/'stress'/'metadata.json').exists() and not (session/framework/scenario/'stress-skipped.json').exists():errors.append(f'{profile}/{framework}/{scenario}: missing stress outcome')
            invalid=rows[(rows.rejected==True)&(~rows.state.isin(CONTAINER_FAILURES))]
            unresolved = [row for _,row in invalid.iterrows()
                          if reviewed_rejection(ROOT / row.source, row.to_dict()) is None]
            if unresolved:errors.append(f'{profile}: {len(unresolved)} unresolved invalid completed measurements')
            sustained=stress[stress.session_id==sid]
            invalid_stress=sustained[(sustained.rejected==True)&(~sustained.state.isin(CONTAINER_FAILURES))]
            if not invalid_stress.empty:errors.append(f'{profile}: {len(invalid_stress)} invalid sustained measurements')
        for path in session.glob('*/*/stress/metadata.json'):
            meta=json.loads(path.read_text())
            if meta.get('duration_seconds',0)<600:errors.append(str(path)+': stress shorter than ten minutes')
            if require_complete and not meta.get('finished_at'):errors.append(str(path)+': sustained test did not finish')
    report=(ROOT/'reports/benchmark-report.md').read_text(encoding='utf-8')
    for relative in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',report):
        if not (ROOT/'reports'/relative).is_file():errors.append('Missing report image: '+relative)
    artifacts=json.loads((ROOT/'reports/charts/artifacts.json').read_text())
    for artifact in artifacts:
        if artifact['numeric_points']<1:errors.append('Empty chart: '+artifact['path'])
        if not (ROOT/artifact['path']).is_file():errors.append('Missing chart: '+artifact['path'])
    if require_complete:
        for scenario in SCENARIOS:
            filename=f'reports/charts/workloads/{scenario}/throughput-vs-p99.png'
            evidence=next((a for a in artifacts if a['path']==filename),None)
            numeric=set(evidence['frameworks']) if evidence else set()
            failed=set(warmups[(warmups.scenario==scenario)&warmups.session_id.isin(state['sessions'].values())].framework)
            if numeric|failed!=set(FRAMEWORKS):errors.append('Incomplete workload chart: '+scenario)
    if errors:raise RuntimeError('\n'.join(errors))
    selected=data[data.session_id.isin(state['sessions'].values())]
    print(f'Report integrity PASS: {len(selected)} campaign measurements, {len(artifacts)} data-bearing figures')
    return True

if __name__=='__main__':verify()
