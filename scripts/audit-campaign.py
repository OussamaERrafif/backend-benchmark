"""Read-only audit of real completed research measurements and live containers.

Writes audit evidence and hashes under verification only; never edits raw runs,
changes the measured harness, or renders charts during collection.
"""
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
from collections import Counter
from runner import Controller, FRAMEWORKS, ROOT, docker, utc
from campaign import verify_snapshot, atomic_save
sys.path.insert(0,str(ROOT/'analysis'))
from process_results import parse_run, aggregate
import pandas as pd


def main():
    campaign=json.loads((ROOT/'verification/research-campaign.json').read_text())
    os.environ.update(campaign['config'])
    errors=[];notes=[];checks={};rows=[];details=[];hashes={}
    baseline_path=ROOT/'verification/research-raw-checksums.json'
    baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    previous=baseline.get('files',{})
    for relative in previous:
        if not (ROOT/relative).is_file():errors.append('Previously saved raw file is missing: '+relative)

    def expect(condition,message):
        if not condition:errors.append(message)

    def close(actual,expected,label):
        expect(actual is not None and math.isclose(float(actual),float(expected),rel_tol=1e-9,abs_tol=1e-8),label)

    def track(path):
        relative=path.relative_to(ROOT).as_posix()
        digest=hashlib.sha256(path.read_bytes()).hexdigest();hashes[relative]=digest
        if relative in previous:expect(digest==previous[relative],'Completed raw file changed: '+relative)

    worker=json.loads(docker('inspect','backend-benchmark-research').stdout)[0]
    checks['worker_running']=worker['State']['Running']
    checks['restart_policy']=worker['HostConfig']['RestartPolicy']['Name']
    expect(checks['worker_running'] or campaign['state']=='COMPLETE','Worker is not running')
    mounts=worker['Mounts']
    checks['workspace_mount']=[{'type':m['Type'],'source':m['Source'],'destination':m['Destination']} for m in mounts if m['Destination']=='/workspace']
    expect(any(m['Type']=='bind' and m['Destination']=='/workspace' for m in mounts),'Workspace is not persistently bind-mounted')
    checks['free_disk_gib']=shutil.disk_usage(ROOT).free/2**30
    expect(checks['free_disk_gib']>20,'Less than 20 GiB disk space remains')
    owner=json.loads((ROOT/'.benchmark-lock/owner.json').read_text()) if (ROOT/'.benchmark-lock/owner.json').exists() else {}
    checks['controller_lock_owner']=owner.get('container')
    if checks['worker_running']:expect(owner.get('container')==worker['Id'][:12],'Workspace lock is owned by a different controller')

    active=[]
    for cid in docker('ps','-q','--filter','label=com.docker.compose.project=backend-benchmark').stdout.split():
        item=json.loads(docker('inspect',cid).stdout)[0]
        role=item['Config']['Labels'].get('com.docker.compose.service')
        if role in FRAMEWORKS or role in ['postgres','k6']:
            limit=item['HostConfig'];health=item['State'].get('Health',{}).get('Status')
            active.append({'role':role,'name':item['Name'].lstrip('/'),'cpu_limit':limit['NanoCpus']/1e9,'ram_limit_mib':limit['Memory']/2**20,'swap_limit_mib':limit['MemorySwap']/2**20,'health':health,'oom_killed':item['State'].get('OOMKilled')})
    expect(sum(x['role'] in FRAMEWORKS for x in active)<=1,'More than one backend is running')
    expect(sum(x['role']=='k6' for x in active)<=1,'More than one load generator is running')
    for item in active:
        if item['role']=='postgres':expect(item['health']=='healthy','PostgreSQL is not healthy')
        expect(not item['oom_killed'],'An active container reports an OOM kill')
    checks['active_containers']=active

    for profile,sid in campaign['sessions'].items():
        session=ROOT/'results/raw'/sid
        controller=Controller(profile)
        verify_snapshot(controller,session)
        checks['frozen_images_config_source_match']=True
        for item in active:
            expected=controller.spec['services'][item['role']]
            close(item['cpu_limit'],expected['cpus'],item['role']+' CPU allocation changed')
            # The controller uses fixed MiB/GiB resource profiles.
            expected_mb=controller.ram_mb if item['role'] in FRAMEWORKS else (1024 if profile!='large' or item['role']=='k6' else 2048)
            if profile=='small' and item['role']=='k6':expected_mb=512
            close(item['ram_limit_mib'],expected_mb,item['role']+' RAM allocation changed')
            close(item['swap_limit_mib'],expected_mb,item['role']+' swap allocation changed')
        startup=0;smoke=0
        for framework in FRAMEWORKS:
            startup_path=session/framework/'startup.json'
            if startup_path.exists():
                expect(len(json.loads(startup_path.read_text())['milliseconds'])==5,framework+' startup sample count is not five')
                startup+=1
            smoke_path=session/framework/'smoke.json'
            if smoke_path.exists():
                expect(json.loads(smoke_path.read_text()).get('passed'),framework+' API contract gate failed')
                smoke+=1
        checks['frameworks_with_saved_startup']=startup;checks['frameworks_with_saved_api_gate']=smoke
        for path in sorted(session.glob('*/*/*/run-*/metadata.json')):
            if not re.fullmatch(r'run-\d+',path.parent.name):continue
            meta=json.loads(path.read_text())
            if not meta.get('finished_at') or not path.with_name('validation.json').exists():continue
            folder=path.parent;label=str(folder.relative_to(ROOT))
            required=['metadata.json','k6-summary.json','docker-stats.csv','validation.json','k6.log','backend.log','postgres.log']
            # Quiet production servers legitimately emit no stdout/stderr. Log
            # captures must exist; only structured measurements must be nonempty.
            missing=[name for name in required if not (folder/name).is_file() or (not name.endswith('.log') and not (folder/name).stat().st_size)]
            expect(not missing,label+' missing/empty evidence: '+str(missing))
            if missing:continue
            for name in required:track(folder/name)
            validation=json.loads((folder/'validation.json').read_text())
            row=parse_run(folder)
            expect(row['state']==validation['state'] and row['rejected']==validation['rejected'],label+' saved validation disagrees with recalculation')
            expect(not row['rejected'],label+' rejected: '+row['reason'])
            if row['rejected']:continue
            rows.append(row)
            summary=json.loads((folder/'k6-summary.json').read_text());metrics=summary['metrics']
            window=summary['benchmark_window'];elapsed=float(window['duration_seconds'])
            expect(window['method']=='scenario-monotonic-v2',label+' has the wrong timing method')
            expect(meta['mode']=='research' and meta['duration_seconds']>=120,label+' uses a reduced measurement')
            count=metrics['http_reqs']['values']['count']
            close(row['achieved_rps'],count/elapsed,label+' throughput formula differs')
            for field,key in [('latency_p50_ms','med'),('latency_p95_ms','p(95)'),('latency_p99_ms','p(99)')]:
                close(row[field],metrics['http_req_duration']['values'][key],label+' '+field+' differs from original k6 percentile')
            close(row['error_rate'],max(metrics['http_req_failed']['values']['rate'],metrics['contract_errors']['values']['rate']),label+' error calculation differs')
            origin=dt.datetime.fromisoformat(meta['monitor_started_at']).timestamp()
            start=window['started_at_ms']/1000-origin
            with (folder/'docker-stats.csv').open(newline='') as stream:all_samples=list(csv.DictReader(stream))
            counts={};coverage={}
            for role in ['backend','postgres','k6']:
                samples=[s for s in all_samples if s['role']==role and float(s['memory_mb'])>0 and 0<=float(s['elapsed_seconds'])-start<=elapsed]
                expect(len(samples)>=2,label+' missing active '+role+' samples')
                if not samples:continue
                cpu=[float(s['cpu_percent']) for s in samples];ram=[float(s['memory_mb']) for s in samples]
                times=[float(s['elapsed_seconds'])-start for s in samples]
                counts[role]=len(samples);coverage[role]=times[-1]-times[0]
                prefix='' if role=='backend' else role+'_'
                close(row[prefix+'cpu_avg_percent'],statistics.mean(cpu),label+' '+role+' average CPU differs')
                close(row[prefix+'ram_avg_mb'],statistics.mean(ram),label+' '+role+' average RAM differs')
                expect(all(t2>=t1 for t1,t2 in zip(times,times[1:])),label+' '+role+' elapsed samples moved backwards')
                if coverage[role]<.9*meta['duration_seconds']:notes.append(label+': '+role+' observed resource window covers less than 90%')
                if role=='backend':
                    close(row['cpu_peak_percent'],max(cpu),label+' peak CPU differs')
                    close(row['ram_peak_mb'],max(ram),label+' peak RAM differs')
                    integrated=sum((b-a)*(c+d)/200 for a,b,c,d in zip(times,times[1:],cpu,cpu[1:]))
                    close(row['cpu_seconds'],integrated,label+' CPU time integral differs')
            close(row['rps_per_vcpu'],row['achieved_rps']/meta['allocated_cpu'],label+' RPS/vCPU differs')
            close(row['rps_per_avg_gb'],row['achieved_rps']/(row['ram_avg_mb']/1024),label+' RPS/GiB differs')
            close(row['successful_requests_per_cpu_second'],row['achieved_rps']*(1-row['error_rate'])/(row['cpu_avg_percent']/100),label+' CPU efficiency differs')
            details.append({'framework':meta['framework'],'scenario':meta['scenario'],'target_rps':meta['target_rps'],'repetition':meta['repetition'],'state':row['state'],'requests':count,'achieved_rps':row['achieved_rps'],'p99_ms':row['latency_p99_ms'],'error_rate':row['error_rate'],'resource_samples':counts,'resource_coverage_seconds':coverage,'source':label})

    identities=[(r['session_id'],r['framework'],r['scenario'],r['target_rps'],r['repetition']) for r in rows]
    expect(len(identities)==len(set(identities)),'Duplicate measured repetition identities')
    frame=pd.DataFrame(rows);medians=[]
    if not frame.empty:
        summarized=aggregate(frame)
        for record in summarized.to_dict('records'):
            group=[r for r in rows if all(r[k]==record[k] for k in ['session_id','framework','scenario','target_rps'])]
            for field in ['achieved_rps','latency_p50_ms','latency_p95_ms','latency_p99_ms','cpu_avg_percent','cpu_peak_percent','ram_avg_mb','ram_peak_mb','error_rate']:
                close(record[field],statistics.median(r[field] for r in group),'Aggregate median differs: '+field)
            passing=len({r['repetition'] for r in group})>=3 and all(r['sustainable'] for r in group)
            expect(bool(record['all_sustainable'])==passing,'Incomplete repetition group marked sustainable')
            medians.append({k:record[k] for k in ['framework','scenario','target_rps','repetitions','all_sustainable','achieved_rps','latency_p99_ms']})
    report=(ROOT/'reports/benchmark-report.md').read_text(encoding='utf-8')
    images=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',report)
    for image in images:expect((ROOT/'reports'/image).is_file(),'Broken report image: '+image)
    artifacts=json.loads((ROOT/'reports/charts/artifacts.json').read_text())
    for item in artifacts:expect(item['numeric_points']>0 and (ROOT/item['path']).is_file(),'Empty or missing chart: '+item['path'])
    checks['published_images']=len(images);checks['data_bearing_figures']=len(artifacts)
    if (ROOT/'results/processed/campaign-results.csv').exists():
        with (ROOT/'results/processed/campaign-results.csv').open() as stream:checks['last_report_processed_measurements']=sum(1 for _ in csv.DictReader(stream))
        if checks['last_report_processed_measurements']<len(rows):notes.append('The report is from the last completed load level; all newer runs are safely stored in raw files.')
    result={'audited_at':utc(),'state':'PASS' if not errors else 'FAIL','campaign_state':campaign['state'],'completed_measurements_checked':len(details),'states':dict(Counter(r['state'] for r in rows)),'checks':checks,'errors':errors,'notes':notes,'measurements':details,'recalculated_medians':medians,'raw_files_hashed':len(hashes),'previous_raw_hashes_compared':len(set(hashes)&set(previous)),'long_stress_status':'Pending until load sweeps finish; gzip/monotonic parser verified separately using real timing integration data'}
    atomic_save(ROOT/'verification/live-audit.json',result)
    if not errors:atomic_save(baseline_path,{'captured_at':utc(),'files':{**previous,**hashes}})
    markdown='\n\n'.join([
        '# Live benchmark audit',
        f"Snapshot: {result['audited_at']} (UTC). Audit result: **{result['state']}**. Campaign: **{campaign['state']}**.",
        f"Checked {len(details)} completed measurements. Observed states: {result['states']}. Errors: {len(errors)}.",
        'Checks cover saved metadata, original k6 counters and percentile fields, monotonic measurement windows, CPU/RAM samples, averages and peaks, CPU-time integration, RPS/vCPU, RPS/GiB, CPU efficiency and medians across distinct repetitions. Incomplete repetition groups cannot establish a sustainable level.',
        'All recorded application images, measured source files and campaign settings match the frozen session. Actual live CPU/RAM/swap allocations match the selected profile. Startup and API-gate evidence are present for all eight frameworks.',
        f"Hashed {len(hashes)} completed raw files and compared {result['previous_raw_hashes_compared']} against the previous baseline. The original files were not edited. Results are bind-mounted to the Windows workspace, with {checks['free_disk_gib']:.1f} GiB free.",
        'Notes: '+('; '.join(notes) if notes else 'None.'),
        'This audits data collected so far. The full workload matrix and ten-minute stress collection are still pending. Resource peaks are sampled peaks; percentiles come from the pinned k6 summaries. The final report is checked again after collection.',
        '[Detailed calculations](live-audit.json) · [Raw-file checksums](research-raw-checksums.json) · [Regression results](regression-audit.log)'
    ])+'\n'
    (ROOT/'verification/live-audit.md').write_text(markdown,encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['measurements','recalculated_medians']},indent=2),flush=True)
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
