"""Validate measured files; never replace missing or failed measurements with zero."""
import csv
import datetime as dt
import json
import gzip
import math
from pathlib import Path
import statistics
import pandas as pd

ROOT = Path('/workspace') if Path('/workspace').exists() else Path(__file__).resolve().parents[1]
KEYS = ['session_id','framework','framework_version','runtime_version','image_id','profile','scenario','target_rps']
NUMBERS = ['achieved_rps','requests','errors','error_rate','latency_avg_ms','latency_p50_ms','latency_p90_ms','latency_p95_ms','latency_p99_ms','cpu_avg_percent','cpu_peak_percent','cpu_stddev_percent','ram_avg_mb','ram_peak_mb','ram_growth_mb','rps_per_vcpu','rps_per_avg_gb','rps_per_peak_gb','successful_requests_per_cpu_second','postgres_cpu_avg_percent','postgres_ram_avg_mb','k6_cpu_avg_percent','k6_ram_avg_mb','dropped_iterations']
BASE = ['timestamp',*KEYS,'allocated_cpu','allocated_ram_mb','workers','db_pool_max',*NUMBERS,'sustainable','repetition','mode','kind','state','rejected','reason','required_repetitions','source','cpu_seconds']

# An OOM kill is attributed to the container that was killed: a load generator
# or database running out of memory says nothing about the backend's memory use.
OOM_STATES = {'backend':'OUT_OF_MEMORY','postgres':'DATABASE_OOM','k6':'LOAD_GENERATOR_OOM'}
OOM_REASONS = {'OUT_OF_MEMORY':'The backend container was killed by the OOM manager',
               'DATABASE_OOM':'The PostgreSQL container was killed by the OOM manager',
               'LOAD_GENERATOR_OOM':'The k6 load generator was killed by the OOM manager'}
# Container-level failures: rejected without numbers, but valid experimental outcomes.
CONTAINER_FAILURES = ['OUT_OF_MEMORY','DATABASE_OOM','LOAD_GENERATOR_OOM','BACKEND_CRASH']

def oom_state(evidence):
    return next((OOM_STATES[role] for role in OOM_STATES if evidence.get(role+'_state',{}).get('OOMKilled')),None)

def metric(summary, name, key):
    value = float(summary['metrics'][name]['values'][key])
    if not math.isfinite(value): raise ValueError(f'Non-finite {name}/{key}')
    return value

def parse_run(directory):
    directory = Path(directory)
    row = {'source':str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory),'state':'BENCHMARK_ERROR','rejected':True,'sustainable':False,'reason':''}
    try:
        meta = json.loads((directory/'metadata.json').read_text())
        row.update({k:meta.get(k) for k in ['timestamp','framework','profile','allocated_cpu','allocated_ram_mb','workers','db_pool_max','scenario','target_rps','repetition','mode','kind']})
        session = next(p for p in directory.parents if (p/'session.json').exists())
        settings = json.loads((session/'session.json').read_text())
        row['session_id']=session.name
        row['required_repetitions']=int(settings['config']['REPETITIONS'])
        snapshot=json.loads((session/'versions.json').read_text())
        versions=snapshot[meta['framework']]
        row['framework_version']=versions['framework'];row['runtime_version']=versions['runtime']
        row['image_id']=snapshot.get('resolved_images',{}).get(meta['framework'],{}).get('id','unknown')
        if (directory/'interrupted.json').exists():
            row['kind']='interrupted';raise ValueError('Measurement interrupted before completion; preserved outside numeric repetitions')
        if not meta.get('backend_state'):raise ValueError('Missing backend state; execution did not produce complete evidence')
        state=oom_state(meta)
        if state:
            row['state']=state;raise ValueError(OOM_REASONS[state])
        if not meta.get('backend_state',{}).get('Running',False):
            row['state']='BACKEND_CRASH';raise ValueError('Backend was not running at completion')
        if meta.get('postgres_state',{}).get('Health',{}).get('Status')!='healthy':raise ValueError('PostgreSQL was not healthy at completion')
        if meta.get('k6_exit_code') not in [0,99]:raise ValueError('k6 execution failed: '+str(meta.get('k6_exit_code')))
        summary=json.loads((directory/'k6-summary.json').read_text())
        requests=metric(summary,'http_reqs','count')
        if requests<=0:raise ValueError('No HTTP requests completed')
        failed=metric(summary,'http_req_failed','passes')
        error_rate=metric(summary,'http_req_failed','rate')
        if not 0<=error_rate<=1 or abs(error_rate*requests-failed)>1:raise ValueError('HTTP error counts disagree')
        contract_rate=metric(summary,'contract_errors','rate')
        if not 0<=contract_rate<=1:raise ValueError('Invalid contract error rate')
        # Contract failures include wrong success bodies; HTTP error metrics alone miss them.
        row.update(requests=int(requests),errors=int(round(max(failed,contract_rate*requests))),error_rate=max(error_rate,contract_rate),achieved_rps=metric(summary,'http_reqs','rate'))
        for suffix,key in [('avg','avg'),('p50','med'),('p90','p(90)'),('p95','p(95)'),('p99','p(99)')]:
            row[f'latency_{suffix}_ms']=metric(summary,'http_req_duration',key)
        if row['latency_avg_ms']<=0 or min(row[f'latency_{n}_ms'] for n in ['p50','p90','p95','p99'])<0:raise ValueError('Nonpositive latency measurement')
        if not row['latency_p50_ms']<=row['latency_p90_ms']<=row['latency_p95_ms']<=row['latency_p99_ms']:raise ValueError('Latency percentiles are out of order')
        seconds=float(meta['duration_seconds'])
        window=summary.get('benchmark_window')
        if meta.get('throughput_window') in ['scenario-v1','scenario-monotonic-v2']:
            if not window or window.get('method')!=meta['throughput_window']:raise ValueError('Missing scenario measurement window')
            elapsed=float(window['duration_seconds'])
            start=float(window['started_at_ms'])/1000
            end=float(window['finished_at_ms'])/1000
            if not all(map(math.isfinite,[elapsed,start,end])) or abs(end-start-elapsed)>.01 or abs(float(window['scheduled_duration_seconds'])-seconds)>.001 or not seconds<=elapsed<=seconds+11:
                raise ValueError('Invalid scenario measurement window')
            row['achieved_rps']=requests/elapsed
        if requests>meta['target_rps']*seconds*1.02+1:raise ValueError('Request count exceeds the scheduled arrival-rate budget')
        if row['achieved_rps']>meta['target_rps']*1.1:raise ValueError('Impossible achieved throughput')
        stats=pd.read_csv(directory/'docker-stats.csv')
        if meta.get('throughput_window')=='scenario-monotonic-v2':
            origin=dt.datetime.fromisoformat(meta['monitor_started_at']).timestamp()
            stats['scenario_elapsed_seconds']=stats['elapsed_seconds']-(start-origin)
            stamps=stats['scenario_elapsed_seconds']
            if not stamps.map(math.isfinite).all():raise ValueError('Invalid monotonic resource timestamps')
            stats=stats[(stamps>=0)&(stamps<=elapsed)]
        elif meta.get('throughput_window')=='scenario-v1':
            stamps=pd.to_datetime(stats['timestamp'],utc=True).astype('int64')/1e9
            stats=stats[(stamps>=start)&(stamps<=end)]
        for role,prefix in [('backend',''),('postgres','postgres_'),('k6','k6_')]:
            samples=stats[stats['role']==role].copy()
            if len(samples)<2:raise ValueError(f'Missing resource samples for {role}')
            for field in ['cpu_percent','memory_mb']:
                if not samples[field].map(math.isfinite).all() or (samples[field]<0).any():raise ValueError('Invalid resource measurement')
            if (samples['memory_mb']<=0).all():raise ValueError('No memory measurement')
            # Docker returns all-zero stats after a container exits; those are not
            # active-process measurements and must not lower average CPU/RAM.
            samples=samples[samples['memory_mb']>0]
            if len(samples)<2:raise ValueError(f'Missing active resource samples for {role}')
            if role=='backend':
                times=samples['scenario_elapsed_seconds'].to_numpy() if 'scenario_elapsed_seconds' in samples else pd.to_datetime(samples['timestamp'],utc=True).astype('int64').to_numpy()/1e9
                if (pd.Series(times).diff().dropna()<0).any():raise ValueError('Resource timestamps moved backwards')
                if times[-1]-times[0]<min(seconds*.5,seconds-3):raise ValueError('Resource samples do not cover enough of the measurement')
                # Trapezoid integration over observed intervals, no extrapolation beyond samples.
                cpu=samples['cpu_percent'].to_numpy()/100
                cpu_seconds=sum((times[i]-times[i-1])*(cpu[i]+cpu[i-1])/2 for i in range(1,len(times)))
                row.update(cpu_avg_percent=float(samples.cpu_percent.mean()),cpu_peak_percent=float(samples.cpu_percent.max()),ram_avg_mb=float(samples.memory_mb.mean()),ram_peak_mb=float(samples.memory_mb.max()),cpu_seconds=cpu_seconds)
                row['cpu_stddev_percent']=float(samples.cpu_percent.std(ddof=1))
                row['ram_growth_mb']=float(samples.memory_mb.tail(3).mean()-samples.memory_mb.head(3).mean())
            else:
                row[prefix+'cpu_avg_percent']=float(samples.cpu_percent.mean());row[prefix+'ram_avg_mb']=float(samples.memory_mb.mean())
        row['rps_per_vcpu']=row['achieved_rps']/meta['allocated_cpu']
        row['rps_per_avg_gb']=row['achieved_rps']/(row['ram_avg_mb']/1024)
        row['rps_per_peak_gb']=row['achieved_rps']/(row['ram_peak_mb']/1024)
        row['successful_requests_per_cpu_second']=row['achieved_rps']*(1-row['error_rate'])/(row['cpu_avg_percent']/100) if row['cpu_avg_percent']>0 else None
        row['dropped_iterations']=summary['metrics'].get('dropped_iterations',{}).get('values',{}).get('count',0)
        limits=meta['thresholds']
        achieved=row['achieved_rps']>=meta['target_rps']*limits['MIN_TARGET_ACHIEVEMENT']
        errors=row['error_rate']<limits['MAX_ERROR_RATE']
        latency=row['latency_p95_ms']<limits['MAX_P95_MS'] and row['latency_p99_ms']<limits['MAX_P99_MS']
        if not meta.get('backend_healthy',False):row['state']='HEALTH_CHECK_FAILED';row['reason']='Backend remained running but the post-load health probe failed'
        elif row['k6_cpu_avg_percent']>=meta['k6_cpu']*90 and (not achieved or row['dropped_iterations']>0):row['state']='LOAD_GENERATOR_LIMITED'
        elif (meta['scenario'].startswith('db-') or meta['scenario']=='mixed') and row['postgres_cpu_avg_percent']>=meta['postgres_cpu']*90 and row['cpu_avg_percent']<meta['allocated_cpu']*90 and (not achieved or not latency or not errors):row['state']='DATABASE_LIMITED'
        elif not errors:row['state']='ERROR_LIMIT'
        elif not latency:row['state']='LATENCY_LIMIT'
        elif not achieved:row['state']='THROUGHPUT_LIMIT';row['reason']='Target shortfall; inspect dropped iterations, backend queues and VU/memory limits'
        else:row['state']='PASS'
        row['sustainable']=row['state']=='PASS';row['rejected']=False
    except (OSError,ValueError,KeyError,TypeError,StopIteration) as e:
        row['reason']=str(e)
    return row

def aggregate(data):
    rows=[]
    if data.empty:return pd.DataFrame(columns=KEYS+['repetitions','all_sustainable'])
    for keys,group in data.groupby(KEYS,dropna=False):
        out=dict(zip(KEYS,keys));out['repetitions']=len(group)
        needed=int(group['required_repetitions'].max())
        out['required_repetitions']=needed
        out['all_sustainable']=len(group)>=needed and group['repetition'].nunique()>=needed and group['sustainable'].all() and not group['rejected'].any()
        valid=group[group['rejected']==False]
        out['valid_repetitions']=len(valid)
        out['rejected_repetitions']=int(group['rejected'].sum())
        for field in NUMBERS:
            values=pd.to_numeric(valid.get(field,pd.Series(dtype=float)),errors='coerce').dropna()
            if len(values):
                out[field]=float(values.median())
                for stat,value in [('mean',values.mean()),('stddev',values.std(ddof=1) if len(values)>1 else None),('min',values.min()),('max',values.max())]:out[field+'_'+stat]=value
        out['states']=','.join(sorted(set(group.state)))
        rows.append(out)
    return pd.DataFrame(rows)

def sustainable(summary):
    cols=['session_id','framework','framework_version','runtime_version','image_id','profile','scenario','max_sustainable_rps','achieved_rps','latency_p95_ms','latency_p99_ms','cpu_avg_percent','ram_avg_mb','rps_per_vcpu','rps_per_avg_gb']
    rows=[]
    if not summary.empty:
        passing=summary[summary['all_sustainable']==True]
        for _,group in passing.groupby(['session_id','framework','image_id','profile','scenario']):
            row=group.sort_values('target_rps').iloc[-1].to_dict()
            row['max_sustainable_rps']=row['target_rps'];rows.append({k:row.get(k) for k in cols})
    return pd.DataFrame(rows,columns=cols)

def stress_windows(directory):
    buckets={};first=None
    summary=json.loads((directory/'k6-summary.json').read_text())
    window=summary.get('benchmark_window')
    seconds=float(window['duration_seconds']) if window else float(json.loads((directory/'metadata.json').read_text())['duration_seconds'])
    if window:first=float(window['started_at_ms'])/1000
    compressed=directory/'k6-timeseries.json.gz'
    source=compressed if compressed.exists() else directory/'k6-timeseries.json'
    pending={}
    monotonic=window and window.get('method')=='scenario-monotonic-v2'
    with (gzip.open(source,'rt') if compressed.exists() else source.open()) as stream:
        for line in stream:
            value=json.loads(line)
            if value.get('type')!='Point':continue
            point=value['data']
            if monotonic:
                if value['metric'] not in ['http_req_duration','http_reqs','http_req_failed','contract_errors','benchmark_completion_elapsed_ms']:continue
                vu=point.get('metadata',{}).get('vu',point.get('tags',{}).get('vu'))
                if vu is None:raise ValueError('Sustained monotonic points require a VU identity')
                item=pending.setdefault(vu,{'latencies':[],'requests':0,'errors':[]})
                if value['metric']=='http_req_duration':item['latencies'].append(point['value'])
                elif value['metric']=='http_reqs':item['requests']+=point['value']
                elif value['metric'] in ['http_req_failed','contract_errors']:item['errors'].append(point['value'])
                elif value['metric']=='benchmark_completion_elapsed_ms':
                    index=int(max(0,point['value'])//60000)
                    bucket=buckets.setdefault(index,{'latencies':[],'requests':0,'errors':[]})
                    bucket['latencies'].extend(item['latencies']);bucket['requests']+=item['requests']
                    if item['errors']:bucket['errors'].append(max(item['errors']))
                    pending.pop(vu,None)
                continue
            stamp=dt.datetime.fromisoformat(point['time'].replace('Z','+00:00')).timestamp()
            if first is None:first=stamp
            if stamp<first:continue
            bucket=buckets.setdefault(int((stamp-first)//60),{'latencies':[],'requests':0,'errors':[]})
            if value['metric']=='http_req_duration':bucket['latencies'].append(point['value'])
            elif value['metric']=='http_reqs':bucket['requests']+=point['value']
            elif value['metric']=='http_req_failed':bucket['errors'].append(point['value'])
    windows=[]
    for index,bucket in sorted(buckets.items()):
        if not bucket['latencies']:continue
        # Partial final windows are labeled rather than treated as full-minute throughput.
        elapsed=min(60,max(0,seconds-index*60))
        windows.append({'minute':index+1,'window_seconds':elapsed,'rps':bucket['requests']/elapsed if elapsed>0 else None,'p99_ms':pd.Series(bucket['latencies']).quantile(.99),'error_rate':statistics.mean(bucket['errors']) if bucket['errors'] else None})
    return windows

def process(root=ROOT):
    output=root/'results/processed';output.mkdir(parents=True,exist_ok=True)
    directories=sorted((root/'results/raw').glob('*/*/*/*/run-*'))+sorted((root/'results/raw').glob('*/*/*/stress'))+sorted((root/'results/raw').glob('*/*/*/*/aborted-run-*'))+sorted((root/'results/raw').glob('*/*/*/aborted-stress-*'))
    # Active measurements are pending, not crashes or corrupt runs. Finished failures
    # still enter the CSV even if they have no successful response metrics.
    rows=[parse_run(p) for p in directories if (p/'metadata.json').exists() and (json.loads((p/'metadata.json').read_text()).get('finished_at') or (p/'interrupted.json').exists())]
    data=pd.DataFrame(rows).reindex(columns=BASE)
    data.to_csv(output/'all-runs.csv',index=False)
    research=data[(data['mode']=='research') & (data['kind']=='normal')]
    quick=data[data['mode']=='quick']
    research.to_csv(output/'results.csv',index=False);quick.to_csv(output/'quick-results.csv',index=False)
    data[data['rejected']==True].to_csv(output/'rejected-runs.csv',index=False)
    summary=aggregate(research);summary.to_csv(output/'summary.csv',index=False)
    maximum=sustainable(summary);maximum.to_csv(output/'max-sustainable-rps.csv',index=False)
    quick_summary=aggregate(quick[quick['kind']=='normal']);quick_summary.to_csv(output/'quick-summary.csv',index=False)
    warmup_failures=[json.loads(path.read_text()) for path in sorted((root/'results/raw').glob('*/*/*/*/warmup-failed-*.json'))]
    pd.DataFrame(warmup_failures,columns=['timestamp','session_id','framework','profile','scenario','target_rps','repetition','state','phase','reason','source']).to_csv(output/'warmup-failures.csv',index=False)
    startup=[]
    for path in sorted((root/'results/raw').glob('*/*/startup.json')):
        item=json.loads(path.read_text());session=json.loads((path.parent.parent/'session.json').read_text())
        startup.append({'session_id':path.parent.parent.name,'framework':path.parent.name,'profile':session['profile'],'mode':session['mode'],'startup_median_ms':statistics.median(item['milliseconds']),'startup_mean_ms':statistics.mean(item['milliseconds']),'startup_stddev_ms':statistics.stdev(item['milliseconds']),'startup_min_ms':min(item['milliseconds']),'startup_max_ms':max(item['milliseconds']),'image_size_mb':item['local_image_size_bytes']/2**20})
    pd.DataFrame(startup,columns=['session_id','framework','profile','mode','startup_median_ms','startup_mean_ms','startup_stddev_ms','startup_min_ms','startup_max_ms','image_size_mb']).to_csv(output/'startup.csv',index=False)
    stress=[]
    for path in sorted((root/'results/raw').glob('*/*/*/stress')):
        try:
            meta=json.loads((path/'metadata.json').read_text())
            source=path/('k6-timeseries.json.gz' if (path/'k6-timeseries.json.gz').exists() else 'k6-timeseries.json')
            stat=source.stat()
            cache=output/'stress-cache'/path.relative_to(root/'results/raw')/'windows.json'
            signature={'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'duration_seconds':meta['duration_seconds'],'summary_mtime_ns':(path/'k6-summary.json').stat().st_mtime_ns,'window_parser':'monotonic-v2'}
            cached=json.loads(cache.read_text()) if cache.exists() else {}
            if cached.get('signature')==signature:windows=cached['windows']
            else:
                windows=stress_windows(path);cache.parent.mkdir(parents=True,exist_ok=True)
                cache.write_text(json.dumps({'signature':signature,'windows':windows})+'\n')
            for window in windows:stress.append({'session_id':path.parents[2].name,'framework':meta['framework'],'profile':meta['profile'],'scenario':meta['scenario'],**window})
        except (OSError,ValueError,KeyError):continue
    pd.DataFrame(stress,columns=['session_id','framework','profile','scenario','minute','window_seconds','rps','p99_ms','error_rate']).to_csv(output/'stress-windows.csv',index=False)
    stress_data=data[data['kind']=='stress'].copy()
    for index,row in stress_data.iterrows():
        samples=[r for r in stress if r['session_id']==row.session_id and r['framework']==row.framework and r['profile']==row.profile and r['scenario']==row.scenario and r['window_seconds']==60]
        if len(samples)>1:
            stress_data.loc[index,'p99_drift_ms']=samples[-1]['p99_ms']-samples[0]['p99_ms']
            stress_data.loc[index,'throughput_stddev_rps']=statistics.stdev(r['rps'] for r in samples)
    stress_data.to_csv(output/'stress-results.csv',index=False)
    print(f'Processed {len(data)} measured runs: {len(research)} research, {len(quick)} quick, {int((data.rejected==True).sum())} rejected')
    return data,summary,maximum

if __name__=='__main__':process()
