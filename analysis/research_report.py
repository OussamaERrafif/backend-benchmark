"""Measured research report with coverage, comparisons and readable offline HTML."""
import datetime as dt
import html
import json
from pathlib import Path
import re
import pandas as pd
from process_results import ROOT, CONTAINER_FAILURES
from experiment_audit import FRAMEWORKS, SCENARIOS

def table(frame,columns):
    if frame.empty:return 'Measurements pending for this section.'
    def cell(value):
        if isinstance(value,(list,tuple)):
            return ', '.join(cell(item) for item in value) if value else '—'
        if pd.api.types.is_scalar(value) and pd.isna(value):return '—'
        return (f'{value:.3f}' if isinstance(value,float) else str(value)).replace('|','\\|').replace('\n',' ')
    labels={'framework':'Framework','profile':'Profile','scenario':'Workload','target_rps':'Target RPS','achieved_rps':'Achieved RPS','latency_p50_ms':'p50 (ms)','latency_p95_ms':'p95 (ms)','latency_p99_ms':'p99 (ms)','error_percent':'Errors (%)','cpu_avg_percent':'Avg CPU (%)','cpu_peak_percent':'Peak CPU (%)','ram_avg_mb':'Avg RAM (MiB)','ram_peak_mb':'Peak RAM (MiB)','repetitions':'Measured reps','valid_repetitions':'Valid reps','all_sustainable':'Sustainable','states':'Observed states','max_sustainable_rps':'Highest passing target RPS','startup_median_ms':'Startup median (ms)','startup_min_ms':'Startup min (ms)','startup_max_ms':'Startup max (ms)','startup_stddev_ms':'Startup SD (ms)','image_size_mb':'Local image (MiB)'}
    return '\n'.join(['| '+' | '.join(labels.get(column,column.replace('_',' ').capitalize()) for column in columns)+' |','| '+' | '.join(['---']*len(columns))+' |']+['| '+' | '.join(cell(v) for v in row)+' |' for row in frame.reindex(columns=columns).itertuples(index=False,name=None)])

def figure(title,relative):
    return f'![{title}]({relative})' if (ROOT/'reports'/relative).is_file() else f'{title}: no qualifying measurements yet; no placeholder image is published.'

def saturation(summary):
    rows=[]
    for (sid,framework,profile,scenario),group in summary.groupby(['session_id','framework','profile','scenario']):
        passing=group[group.all_sustainable==True].sort_values('target_rps')
        limited=group[group.states!='PASS'].sort_values('target_rps')
        rows.append(dict(session_id=sid,framework=framework,profile=profile,scenario=scenario,
            highest_passing_target_rps=passing.target_rps.max() if not passing.empty else None,
            first_observed_limit_rps=limited.target_rps.min() if not limited.empty else None,
            first_limit_states=limited.iloc[0].states if not limited.empty else 'No limit observed within tested ladder',
            highest_tested_target_rps=group.target_rps.max(),load_levels=len(group),
            complete_levels=int((group.valid_repetitions>=group.required_repetitions).sum())))
    return pd.DataFrame(rows,columns=['session_id','framework','profile','scenario','highest_passing_target_rps','first_observed_limit_rps','first_limit_states','highest_tested_target_rps','load_levels','complete_levels'])

def common_load(summary):
    points=[]
    for (sid,profile,scenario),group in summary.groupby(['session_id','profile','scenario']):
        passing=group[group.all_sustainable==True]
        eligible=[target for target,level in passing.groupby('target_rps') if set(level.framework)==set(FRAMEWORKS)]
        if eligible:
            # The highest common passing load compares resource use at equal work.
            points.append(passing[passing.target_rps==max(eligible)])
    return pd.concat(points,ignore_index=True) if points else summary.iloc[:0].copy()

def render_html(markdown,state):
    lines=markdown.splitlines();blocks=[];navigation=[];index=0
    def inline(text):
        text=html.escape(text)
        text=re.sub(r'`([^`]+)`',r'<code>\1</code>',text)
        return re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',text)
    while index<len(lines):
        line=lines[index]
        if not line.strip():index+=1;continue
        if line.startswith('```'):
            code=[];index+=1
            while index<len(lines) and not lines[index].startswith('```'):code.append(lines[index]);index+=1
            blocks.append('<pre><code>'+html.escape('\n'.join(code))+'</code></pre>');index+=1;continue
        heading=re.match(r'^(#{1,3}) (.+)$',line)
        if heading:
            level=len(heading[1]);title=heading[2];anchor=f'section-{index}'
            blocks.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
            if level==2:navigation.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
            index+=1;continue
        image=re.match(r'^!\[([^\]]+)\]\(([^)]+)\)$',line)
        if image:
            blocks.append(f'<figure><img loading="lazy" src="{html.escape(image[2])}" alt="{html.escape(image[1])}"><figcaption>{html.escape(image[1])}</figcaption></figure>');index+=1;continue
        if line.startswith('|'):
            rows=[]
            while index<len(lines) and lines[index].startswith('|'):
                cells=re.split(r'(?<!\\)\|',lines[index].strip().strip('|'))
                if not all(re.fullmatch(r'\s*:?-+:?\s*',cell) for cell in cells):rows.append([cell.strip().replace('\\|','|') for cell in cells])
                index+=1
            head='<tr>'+''.join('<th>'+inline(cell)+'</th>' for cell in rows[0])+'</tr>'
            body=''.join('<tr>'+''.join('<td>'+inline(cell)+'</td>' for cell in row)+'</tr>' for row in rows[1:])
            blocks.append('<div class="table"><table><thead>'+head+'</thead><tbody>'+body+'</tbody></table></div>');continue
        paragraph=[]
        while index<len(lines) and lines[index].strip() and not lines[index].startswith(('#','|','```','![')):paragraph.append(lines[index]);index+=1
        blocks.append('<p>'+inline(' '.join(paragraph))+'</p>')
    css='body{margin:0;background:#f5f7fb;color:#172338;font:16px/1.6 system-ui}aside{position:fixed;width:235px;inset:0 auto 0 0;overflow:auto;background:#122039;padding:22px;box-sizing:border-box}aside a{display:block;color:#c9d6ec;text-decoration:none;margin:12px 0;font-size:13px}main{margin-left:235px;padding:35px 4vw;max-width:1450px}h1{font-size:32px}h2{margin-top:50px;border-top:1px solid #dce3ed;padding-top:25px}h3{margin-top:28px}.banner{padding:14px 20px;border-radius:8px;background:#fff0cc}.COMPLETE{background:#d7f2e1}.NEEDS_ATTENTION{background:#ffdadd}.table{overflow:auto;background:white;border:1px solid #dce3ed;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:9px 12px;white-space:nowrap;text-align:left;border-bottom:1px solid #e5ebf4}th{background:#edf2fb}tr:nth-child(even){background:#f9fbff}figure{margin:24px 0;background:white;padding:12px;border:1px solid #dce3ed;border-radius:8px}img{width:100%;height:auto}figcaption{color:#576680;font-size:13px}pre{background:#122039;color:#e3edff;padding:20px;overflow:auto;border-radius:8px}code{font-size:13px}a{color:#165fcc}@media(max-width:900px){aside{position:static;width:auto}aside a{display:inline-block;margin-right:14px}main{margin:0;padding:20px}}'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Backend Framework Research Report</title><style>'+css+'</style><aside><strong style="color:white">Research report</strong>'+''.join(navigation)+'</aside><main><div class="banner '+html.escape(state)+'">Campaign status: '+html.escape(state)+'</div>'+''.join(blocks)+'</main></html>'

def create():
    processed=ROOT/'results/processed'
    campaign_path=ROOT/'verification/research-campaign.json'
    campaign=json.loads(campaign_path.read_text()) if campaign_path.exists() else {}
    state=campaign.get('state','PENDING')
    sessions=set(campaign.get('sessions',{}).values())
    def read(name):
        frame=pd.read_csv(processed/name)
        if 'mode' in frame:frame=frame[frame['mode']=='research']
        if sessions and 'session_id' in frame:frame=frame[frame.session_id.isin(sessions)]
        return frame
    runs=read('all-runs.csv');summary=read('summary.csv');maximum=read('max-sustainable-rps.csv')
    startup=read('startup.csv');stress=read('stress-results.csv');warmup=read('warmup-failures.csv')
    coverage=read('coverage.csv')
    runs[runs.kind=='normal'].to_csv(processed/'campaign-results.csv',index=False)
    summary.to_csv(processed/'campaign-summary.csv',index=False)
    stress.to_csv(processed/'campaign-stress.csv',index=False)
    profiles=campaign.get('profiles',sorted(runs.profile.unique()) if not runs.empty else ['standard'])
    boundaries=[]
    for sid in sessions:
        for path in (ROOT/'results/raw'/sid).glob('*/*/load-boundary.json'):
            item=json.loads(path.read_text());boundaries.append({'session_id':sid,'framework':path.parent.parent.name,'scenario':path.parent.name,**item})
    limit=saturation(summary);limit.to_csv(processed/'saturation.csv',index=False)
    equal=common_load(summary);equal.to_csv(processed/'common-load.csv',index=False)
    timestamp=dt.datetime.now(dt.timezone.utc).isoformat()
    sections=['# Backend Framework Research Report',f'Updated: {timestamp}. Campaign state: **{state}**.',
        '## Executive Summary',
        f'Scope: all eight frameworks × all nine workloads on the {", ".join(profiles)} configuration. Every visited load level receives three repetitions before the load advances. The planned ladder is 50, 100, 250, 500, 1000, 2000, 4000 and 8000 target RPS. Each repetition uses 60 seconds of warm-up and 120 seconds of measurement, followed by 30 seconds of cooldown. Ten-minute sustained tests follow the completed load sweeps.',
        f'{len(runs[runs.kind=="normal"])} completed load measurements, {len(summary)} summarized framework/load combinations, {len(maximum)} combinations with a passing three-repetition level, and {len(stress)} sustained-load measurements are currently recorded.']
    if state!='COMPLETE':sections.append('Collection is in progress. This document updates after completed load levels; incomplete coverage and repetition counts remain visible. It is not the final completed research report.')
    if campaign.get('error'):sections.append('Campaign requires attention: '+campaign['error'])
    if not maximum.empty:
        overview=[]
        for (profile,scenario),group in maximum.groupby(['profile','scenario']):
            best=group.max_sustainable_rps.max();leaders=', '.join(group[group.max_sustainable_rps==best].framework)
            overview.append({'profile':profile,'scenario':scenario,'highest_passing_target_rps':best,'frameworks_at_that_level':leaders,'frameworks_with_passing_level':group.framework.nunique()})
        sections.append(table(pd.DataFrame(overview),['profile','scenario','highest_passing_target_rps','frameworks_at_that_level','frameworks_with_passing_level']))
        sections.append('These are highest tested passing targets, not interpolated capacities. A framework reaching the top of the ladder has a lower-bound capacity result. A load-generator or database limit does not establish the backend’s intrinsic saturation point. Results apply to the recorded machine, images and workload configurations.')
    sections+=['## Coverage and Completion',table(coverage,['profile','scenario','framework','normal_runs','valid_runs','passing_runs','failed_runs','rejected_runs','target_levels','status']),figure('Every measured load state','charts/all-load-levels.png')]
    sections+=['## Machine and Reproducibility']
    for sid in sorted(sessions or set(runs.session_id.dropna())):
        folder=ROOT/'results/raw'/sid
        sections.append('### Session '+sid+'\n\n```json\n'+(folder/'host.json').read_text().strip()+'\n```')
        sections.append('Recorded settings:\n\n```json\n'+(folder/'session.json').read_text().strip()+'\n```')
    sections.append('Exact declared software versions and driver/pool choices:\n\n```json\n'+(ROOT/'versions.lock').read_text().strip()+'\n```')
    sections.append('Actual Docker image IDs, runtime version observations and dependency/source fingerprints are retained per session and in [provenance.csv](../results/processed/provenance.csv). The random session suffix is not a build hash. Sessions and builds remain separate in each curve.')
    sections+=['## Measurement Definitions',
        'Tables and plotted points use medians across valid repetitions, including genuine threshold failures. Whiskers show their minimum–maximum range; they are not confidence intervals. p50/p95/p99 are per-run request latency percentiles in milliseconds. Average and peak CPU/RAM columns are medians of those per-run statistics. Error percentage includes failed HTTP requests and incorrect response bodies. Achieved RPS includes completed failed attempts/timeouts; successful throughput equals achieved RPS × (1 − error rate). CPU 100% is one logical CPU; RAM is Docker cache-adjusted MiB.',
        'Achieved RPS divides completed requests by the monotonic workload window: the scheduled arrival duration plus any observed final HTTP request drain. Completion time combines k6’s monotonic scheduler progress at request start with HTTP blocked and duration timings. CPU/RAM sampling and CPU-time integration also use monotonic elapsed time. Raw summaries retain the original k6 rate and observed wall-clock drift. VM clock synchronization and initialization therefore do not distort throughput.',
        'A level is sustainable only if every required repetition has errors <1%, p95 <100 ms, p99 <250 ms, and achieved RPS ≥98% of target. Circle = sustainable; triangle = latency exceeded; cross = error limit; square = crash/OOM. Generator, database, throughput and health limits have distinct markers. Missing/invalid numeric measurements are never replaced with zero. Interrupted attempts are retained separately and excluded from completed repetition medians.',
        'One backend is measured at a time. Standard configuration: backend 2 CPUs / 2 GiB, PostgreSQL 1 CPU / 1 GiB, k6 2 CPUs / 1 GiB. CPU, RAM and equal memory+swap quotas are inspected by the controller. Every implementation passes the same 22 API contract checks before measurement. Database writes introduced by warm-up are reset before measured writes; the deterministic 100000-row database is reset between repetitions. Five separate startup samples are recorded.',
        'Adaptive stopping occurs only after completing the repetitions for a level: two consecutive non-sustainable levels, or a critical crash/OOM/health failure. Higher unattempted loads are explicitly recorded as safety cutoffs. Stress runs use 80% of the highest level passing all repetitions. If no level passes, their omission is recorded with a reason.']
    for scenario in SCENARIOS:
        frame=summary[summary.scenario==scenario].copy()
        frame['error_percent']=frame.get('error_rate',pd.Series(dtype=float))*100
        sections+=['## '+scenario+' Results',
            table(frame,['framework','profile','target_rps','achieved_rps','latency_p50_ms','latency_p95_ms','latency_p99_ms','error_percent','cpu_avg_percent','cpu_peak_percent','ram_avg_mb','ram_peak_mb','repetitions','valid_repetitions','states','all_sustainable'])]
        if not frame.empty:
            for name,title in [('throughput-vs-p99','p99 versus achieved throughput'),('throughput-vs-cpu','CPU versus achieved throughput'),('throughput-vs-memory','RAM versus achieved throughput'),('all-load-levels','All measured load states')]:sections.append(figure(scenario+' — '+title,f'charts/workloads/{scenario}/{name}.png'))
    sections+=['## Saturation and Safety Boundaries',table(limit,['framework','profile','scenario','highest_passing_target_rps','first_observed_limit_rps','first_limit_states','highest_tested_target_rps','load_levels','complete_levels']),table(pd.DataFrame(boundaries),['framework','scenario','target_rps','states','rule','higher_loads_skipped'])]
    sections+=['## Resource Efficiency at Equal Load',
        'For each workload, use the highest target load that every framework passes with all repetitions. This compares CPU/RAM while doing the same scheduled work, rather than comparing each framework at a different maximum load.',
        table(equal,['framework','profile','scenario','target_rps','achieved_rps','cpu_avg_percent','ram_avg_mb','rps_per_vcpu','rps_per_avg_gb','successful_requests_per_cpu_second'])]
    sections+=['## Maximum Sustainable Load',table(maximum,['framework','profile','scenario','max_sustainable_rps','achieved_rps','latency_p95_ms','latency_p99_ms','cpu_avg_percent','ram_avg_mb']),figure('Highest passing target RPS','charts/max-rps.png')]
    sections+=['## Ten-minute Sustained Load',table(stress,['framework','profile','scenario','target_rps','achieved_rps','latency_p99_ms','error_rate','cpu_avg_percent','cpu_peak_percent','ram_avg_mb','ram_peak_mb','ram_growth_mb','cpu_stddev_percent','p99_drift_ms','throughput_stddev_rps','state']),
        'Per-minute latency, throughput and error windows are in [stress-windows.csv](../results/processed/stress-windows.csv). Partial final drain windows remain in that CSV; sustained curves and drift comparisons use complete minutes. RAM growth compares the final and initial three samples. p99 drift and throughput variation use complete minute windows. Original time-series points are retained in compressed k6 JSON; compression affects the load generator and its resource/limit state is recorded.']
    skipped=[]
    for sid in sessions:
        for path in (ROOT/'results/raw'/sid).glob('*/*/stress-skipped.json'):skipped.append({'framework':path.parent.parent.name,'scenario':path.parent.name,**json.loads(path.read_text())})
    if skipped:sections.append(table(pd.DataFrame(skipped),['framework','scenario','reason']))
    for scenario in sorted(stress.scenario.unique()):
        for name,title in [('p99','Per-minute p99'),('throughput','Per-minute throughput'),('cpu','CPU over time'),('memory','RAM over time')]:
            sections.append(figure(scenario+' — '+title,f'charts/sustained/{scenario}/{name}.png'))
    sections+=['## Startup and Image Size',table(startup,['framework','profile','startup_median_ms','startup_min_ms','startup_max_ms','startup_stddev_ms','image_size_mb']),figure('Five-sample startup time','charts/startup-time.png'),figure('Local uncompressed image size','charts/docker-image-size.png')]
    scaling=pd.read_csv(processed/'scaling.csv')
    if not scaling.empty:sections+=['## Cross-configuration Scaling',figure('CPU scaling','charts/cpu-scaling.png'),table(scaling,['framework','scenario','allocated_cpu','baseline_rps','scaled_rps','speedup','scaling_efficiency'])]
    sections+=['## Failed and Interrupted Attempts',table(runs[runs.state!='PASS'],['framework','profile','scenario','kind','target_rps','repetition','state','rejected','reason','source']),
        'Warm-up failures are separate pre-measurement results. A crash during warm-up is a safety boundary, not a fabricated 120-second throughput measurement.',table(warmup,['framework','profile','scenario','target_rps','repetition','state','phase','reason','source'])]
    recoveries=[]
    for sid in sorted(sessions):
        for path in sorted((ROOT/'results/raw'/sid/'recoveries').glob('*.json')):
            item=json.loads(path.read_text());archive=Path(item['archive_source'])
            meta=json.loads((archive/'metadata.json').read_text());evidence=item['evidence']
            recoveries.append({'framework':meta['framework'],'scenario':meta['scenario'],
                'target_rps':meta['target_rps'],'repetition':meta['repetition'],
                'scheduled_seconds':evidence['scheduled_seconds'],
                'rejected_window_seconds':evidence['observed_window_seconds'],
                'clock_gap_seconds':evidence['resource_clock_discontinuity']['clock_gap_seconds'],
                'reason':item['reason'],'source':str(archive.relative_to(ROOT))})
    if recoveries:
        sections+=['### Timing recovery',
            'These attempts were rejected by the unchanged measurement validator after a host suspension or large clock discontinuity. Original files and SHA-256 hashes are preserved. Each affected repetition is rerun under the same pinned settings; archived attempts are excluded from numeric medians. Genuine latency, error, generator-limit, crash and OOM results are retained.',
            table(pd.DataFrame(recoveries),['framework','scenario','target_rps','repetition','scheduled_seconds','rejected_window_seconds','clock_gap_seconds','reason','source'])]
    investigations=[]
    for sid in sorted(sessions):
        for path in sorted((ROOT/'results/raw'/sid/'investigations').glob('*.json')):
            item=json.loads(path.read_text())
            investigations.append({**item, 'source':str(path.relative_to(ROOT))})
    if investigations:
        sections+=['### Investigated rejected measurements',
            'These individually investigated attempts remain rejected and are excluded from numeric results. They count as unsuccessful repetitions for adaptive stopping and are not retried. The original files and validation results are preserved and verified against SHA-256 digests. Uninvestigated timing failures still stop the campaign.',
            table(pd.DataFrame(investigations),['relative_source','observed_window_seconds','validator_maximum_seconds','diagnosis','resolution','source'])]
    container_failures=[]
    for frame in [runs,warmup]:
        for row in frame.itertuples(index=False):
            if row.state not in CONTAINER_FAILURES:continue
            path=ROOT/row.source/'metadata.json'
            if not path.exists():continue
            evidence=json.loads(path.read_text())
            for role in ['backend','postgres','k6']:
                observed=evidence.get(role+'_state',{})
                if observed.get('OOMKilled') or (role in ['backend','postgres'] and observed and not observed.get('Running')):
                    container_failures.append({'framework':row.framework,'scenario':row.scenario,'target_rps':row.target_rps,'repetition':row.repetition,'container_role':role,'oom_killed':observed.get('OOMKilled'),'exit_code':observed.get('ExitCode'),'source':row.source})
    if container_failures:
        sections.append('Container failure attribution: an OOM of the load generator or database is an experiment limit, and does not establish backend capacity. Raw metadata identifies the affected container.')
        sections.append(table(pd.DataFrame(container_failures),['framework','scenario','target_rps','repetition','container_role','oom_killed','exit_code','source']))
    sections+=['## Interpretation and Limits',
        'Framework comparisons are conditional on these versions, response serialization, computation, database drivers/pools and server modes. Laravel uses classic per-request framework boot with persistent PDO bounded by PHP threads; pool limits differ and are recorded. Database and generator saturation labels are resource-based heuristics, not causal proof. All containers share the Docker Desktop VM and physical host; sustained runs are sensitive to host contention and thermal/power conditions. Sampled CPU/RAM do not measure instantaneous process peaks or whole-host energy.',
        'This campaign measures the requested standard configuration. Other configurations are not included in its conclusions; cross-configuration scaling needs matched additional measurements and is not represented by an empty graph. No framework-independent or machine-independent ranking follows from this experiment.',
        '## Data and Execution',
        '[Active campaign repetitions](../results/processed/campaign-results.csv) · [Campaign medians and variation](../results/processed/campaign-summary.csv) · [Campaign sustained tests](../results/processed/campaign-stress.csv) · [Coverage](../results/processed/coverage.csv) · [Build provenance](../results/processed/provenance.csv) · [Saturation](../results/processed/saturation.csv) · [Equal-load resource comparisons](../results/processed/common-load.csv) · [All historical runs](../results/processed/all-runs.csv). Historical CSVs retain earlier cohorts; campaign CSVs select only the sessions listed above.',
        'The detached research worker checkpoints each completed measurement and regenerates this report between load levels and after each sustained test. Run `scripts/research-status.ps1` to inspect the actual worker state alongside saved checkpoints, or `docker logs --tail 30 backend-benchmark-research` to read progress. A Docker restart can resume the campaign using the recorded images/configuration; interrupted attempts remain separate. The final integrity check verifies all framework/workload/repetition coverage, stress outcomes, chart points and report image links before the campaign is marked complete.']
    markdown='\n\n'.join(sections)+'\n'
    (ROOT/'reports/benchmark-report.md').write_text(markdown,encoding='utf-8')
    (ROOT/'reports/benchmark-report.html').write_text(render_html(markdown,state),encoding='utf-8')
    print('Research report status: '+state)

if __name__=='__main__':create()
