"""Keep diagnostic evidence separate from the complete research report."""
import json
import pandas as pd
from process_results import ROOT
from research_report import create as research_report, table, figure

def create():
    research_report()
    processed=ROOT/'results/processed'
    data=pd.read_csv(processed/'all-runs.csv');quick=data[data['mode']=='quick']
    startup=pd.read_csv(processed/'startup.csv');startup=startup[startup['mode']=='quick']
    coverage=pd.read_csv(processed/'coverage.csv');coverage=coverage[coverage['mode']=='quick']
    provenance=pd.read_csv(processed/'provenance.csv');provenance=provenance[provenance['mode']=='quick']
    preflight=[]
    for path in sorted((ROOT/'results/raw').glob('*/*/smoke.json')):
        evidence=json.loads(path.read_text())
        if not evidence.get('passed'):preflight.append({'session_id':path.parent.parent.name,'framework':path.parent.name,'reason':evidence.get('error','Smoke failed')})
    sections=['# Benchmark Verification Report — Debugging Only',
        'These are real short diagnostics, separate from [the research report](benchmark-report.md). Failed points remain visible. One short repetition cannot establish research capacity or a saturation curve.',
        '## Coverage audit',
        'NOT_RUN means that framework/workload was never measured. Smoke checks are separate from load measurements.',
        table(coverage,['profile','scenario','framework','normal_runs','stress_runs','valid_runs','passing_runs','failed_runs','rejected_runs','status']),
        '## Session and Build Identity',
        'Session suffixes are random run identifiers, not build hashes. Actual image IDs and source manifests are in [provenance.csv](../results/processed/provenance.csv). Curves and panels keep sessions/builds separate.',
        table(provenance.assign(image_id=lambda frame:frame.image_id.str.replace('sha256:','',regex=False).str[:12]),['session_id','framework','image_id','normal_runs','stress_runs','purpose']),
        '## Measured Diagnostics',
        'Error rate is a fraction, CPU 100% is one logical CPU, RAM is MiB. Achieved RPS includes completed failed attempts/timeouts.',
        table(quick,['session_id','framework','profile','scenario','kind','target_rps','achieved_rps','latency_p50_ms','latency_p95_ms','latency_p99_ms','error_rate','cpu_avg_percent','cpu_peak_percent','ram_avg_mb','ram_peak_mb','state']),
        '## Startup and Image Diagnostics',table(startup,['session_id','framework','profile','startup_median_ms','image_size_mb']),
        '## Failures and Limits',table(quick[quick.state!='PASS'],['session_id','framework','scenario','target_rps','state','rejected','reason']),
        '## Preserved Preflight Failures',table(pd.DataFrame(preflight),['session_id','framework','reason']),
        '## Diagnostic Charts',
        'Circle = observed pass; triangle = latency limit; cross = error limit; square = crash/OOM. Incomplete repetitions and other limits have distinct markers. No empty placeholder charts are published.',
        figure('Every attempted load state','charts/quick/all-load-levels.png'),figure('Latency versus throughput','charts/quick/throughput-vs-p99.png'),figure('CPU versus throughput','charts/quick/throughput-vs-cpu.png'),figure('RAM versus throughput','charts/quick/throughput-vs-memory.png')]
    for sid,frame in quick[quick.kind=='normal'].groupby('session_id',sort=True):
        sections.append('## Separate charts: '+sid+'\n\n'+f'[Latency](charts/quick/sessions/{sid}/throughput-vs-p99.png) · [CPU](charts/quick/sessions/{sid}/throughput-vs-cpu.png) · [RAM](charts/quick/sessions/{sid}/throughput-vs-memory.png) · [All states](charts/quick/sessions/{sid}/all-load-levels.png)')
    for path in sorted((ROOT/'results/raw').glob('*/notes.json')):sections.append('## Session '+path.parent.name+'\n\n'+json.loads(path.read_text(encoding='utf-8-sig')).get('note',''))
    sections.append('## Research Report\n\nSee [the full research campaign](benchmark-report.md) for current collection status.')
    (ROOT/'reports/verification-report.md').write_text('\n\n'.join(sections)+'\n',encoding='utf-8')
    print('Reports: /workspace/reports/benchmark-report.md and benchmark-report.html')

if __name__=='__main__':create()
