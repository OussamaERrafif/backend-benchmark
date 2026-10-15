import math
import json
import textwrap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd
from process_results import ROOT, sustainable
from experiment_audit import FRAMEWORKS

ORDER=FRAMEWORKS
COLORS={name:plt.get_cmap('tab10')(index) for index,name in enumerate(ORDER)}
# The worst observed state marks a level; CSVs retain every repetition's state.
STATES={
    'PASS':('o','Sustainable (all repetitions)'),
    'INCOMPLETE':('D','Incomplete repetitions'),
    'LATENCY_LIMIT':('^','Latency limit'),
    'THROUGHPUT_LIMIT':('>','Throughput shortfall'),
    'ERROR_LIMIT':('x','Error limit'),
    'LOAD_GENERATOR_LIMITED':('P','Load generator limited'),
    'DATABASE_LIMITED':('v','Database limited'),
    'LOAD_GENERATOR_OOM':('P','k6 OOM (harness)'),
    'DATABASE_OOM':('v','PostgreSQL OOM'),
    'HEALTH_CHECK_FAILED':('h','Health check failed'),
    'BENCHMARK_ERROR':('+','Benchmark error'),
    'BACKEND_CRASH':('s','Backend crash'),
    'OUT_OF_MEMORY':('s','Backend OOM'),
}
PRIORITY=list(STATES)
ARTIFACTS=[]

def remove_placeholder(path):
    # Only a known generated file is removed; raw measurements are never modified.
    path.unlink(missing_ok=True)

def point_state(row):
    states=set(str(row.get('states',row.get('state','PASS'))).split(','))
    if states=={'PASS'}:
        return 'PASS' if row.get('all_sustainable',True) else 'INCOMPLETE'
    return max(states,key=lambda state:PRIORITY.index(state) if state in PRIORITY else PRIORITY.index('BENCHMARK_ERROR'))

def panel_groups(data):
    # Random session identifiers are not build hashes. Never join session curves.
    return list(data.groupby(['session_id','profile','scenario'],sort=True)) if not data.empty else [('all',data)]

def missing_label(framework,frame):
    return 'not run' if frame[frame.framework==framework].empty else 'no sustainable level'

def with_warmup_failures(summary,warmups):
    result=summary.copy()
    keys=['session_id','framework','profile','scenario','target_rps']
    for values,group in warmups.groupby(keys):
        mask=pd.Series(True,index=result.index)
        for field,value in zip(keys,values):mask&=result[field]==value
        states=set(group.state)
        if mask.any():
            for index in result.index[mask]:
                result.loc[index,'states']=','.join(sorted(states|set(str(result.loc[index,'states']).split(','))))
                result.loc[index,'warmup_failures']=group.repetition.nunique()
        else:
            session=json.loads((ROOT/'results/raw'/values[0]/'session.json').read_text())
            row={**dict(zip(keys,values)),'repetitions':0,'required_repetitions':int(session['config']['REPETITIONS']),'all_sustainable':False,'states':','.join(sorted(states)),'warmup_failures':group.repetition.nunique()}
            result=pd.concat([result,pd.DataFrame([row])],ignore_index=True)
    return result

def chart(path, data, metric, ylabel, curve=False, x='achieved_rps', title=None, reference=None):
    reference=data if reference is None else reference
    if reference.empty or metric not in data or not pd.to_numeric(data[metric],errors='coerce').notna().any():
        remove_placeholder(path);return False
    panels=panel_groups(reference)
    columns=min(3,len(panels));rows=math.ceil(len(panels)/columns)
    fig,axes=plt.subplots(rows,columns,figsize=(6.5*columns,5.4*rows),squeeze=False,layout='constrained')
    for ax,(key,evidence) in zip(axes.flat,panels):
        frame=data
        if isinstance(key,tuple):
            for field,value in zip(['session_id','profile','scenario'],key):
                if field in frame:frame=frame[frame[field]==value]
        if frame.empty or metric not in frame:
            ax.text(.5,.5,'No eligible measured data',ha='center',va='center',transform=ax.transAxes)
            ax.set_xticks([]);ax.set_yticks([])
        elif curve:
            missing=[]
            for framework in ORDER:
                subset=frame[frame.framework==framework]
                image_groups=subset.groupby('image_id',dropna=False) if 'image_id' in subset else [('unknown',subset)]
                for _,group in image_groups:
                    group=group.sort_values('target_rps' if 'target_rps' in group else x)
                    valid=group.dropna(subset=[x,metric])
                    if not valid.empty:ax.plot(valid[x],valid[metric],color=COLORS[framework],linewidth=1.2,label=framework)
                    for _,row in group.iterrows():
                        state=point_state(row);marker=STATES.get(state,STATES['BENCHMARK_ERROR'])[0]
                        if pd.isna(row.get(x)) or pd.isna(row.get(metric)):
                            missing.append(f'{framework} @{row.get("target_rps","?")}: {state}');continue
                        ax.scatter(row[x],row[metric],marker=marker,color=COLORS[framework],s=48,zorder=3)
                        if row.get('valid_repetitions',0)>1 and metric+'_min' in row:
                            lo=row[metric]-row[metric+'_min'];hi=row[metric+'_max']-row[metric]
                            ax.errorbar(row[x],row[metric],yerr=[[lo],[hi]],fmt='none',ecolor=COLORS[framework],alpha=.5,capsize=3)
            ax.set_xlabel({'cpu':'Allocated vCPUs','target_rps':'Target requests / second','elapsed_minutes':'Minutes since workload start'}.get(x,'Achieved requests / second'))
            handles,labels=ax.get_legend_handles_labels()
            if handles:ax.legend(handles,labels,fontsize=7,loc='best')
            unrun=[f for f in ORDER if evidence[evidence.framework==f].empty]
            notes=[]
            if unrun:notes.append('Not run: '+', '.join(unrun))
            if missing:notes.append('No numeric point: '+'; '.join(missing))
            if 'repetitions' in evidence:
                notes.append('Repetitions per level: '+', '.join(f'{int(n)}/{int(r)}' for n,r in evidence[['repetitions','required_repetitions']].drop_duplicates().itertuples(index=False,name=None)))
            if notes:
                # Matplotlib's automatic text wrapping happens after constrained
                # layout measures artists. Long failure lists otherwise squeeze
                # every plot in the grid into a nearly zero-width column.
                annotation='\n'.join(textwrap.fill(note,width=76,break_long_words=False,
                                                break_on_hyphens=False) for note in notes)
                ax.text(0,-.23,annotation,transform=ax.transAxes,fontsize=7,va='top')
        else:
            positions=[];labels=[];values=[];colors=[]
            for framework in ORDER:
                subset=frame[frame.framework==framework]
                if subset.empty:labels.append(framework+'\n'+missing_label(framework,evidence))
                for _,row in subset.iterrows():
                    if pd.isna(row[metric]):continue
                    positions.append(len(labels));labels.append(framework);values.append(row[metric]);colors.append(COLORS[framework])
            ax.bar(positions,values,color=colors);ax.set_xticks(range(len(labels)),labels,rotation=35,ha='right',fontsize=7)
        if curve and metric=='latency_p99_ms' and metric in frame and (frame[metric].dropna()>0).any():
            ax.set_yscale('log');ax.set_ylabel('p99 latency (ms; log scale)')
        else:ax.set_ylabel(ylabel)
        ax.set_title(f'{key[1]} / {key[2]}\nSession {key[0]}' if isinstance(key,tuple) else str(key),fontsize=9)
        ax.grid(axis='y',alpha=.25)
    for ax in list(axes.flat)[len(panels):]:ax.set_visible(False)
    fig.suptitle(title or path.stem.replace('-',' ').title(),fontsize=14)
    if curve and not data.empty and 'states' in data:
        observed={point_state(row) for _,row in data.iterrows()}
        handles=[Line2D([0],[0],marker=marker,color='black',linestyle='none',label=('Observed pass (debug)' if state=='PASS' and (title or '').startswith('DEBUG ONLY') else label)) for state,(marker,label) in STATES.items() if state in observed]
        fig.legend(handles=handles,loc='outside lower center',ncol=min(4,len(handles)),fontsize=8)
    fig.savefig(path,dpi=140);plt.close(fig)
    ARTIFACTS.append({'path':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),'metric':metric,'numeric_points':int(data[metric].notna().sum()),'frameworks':sorted(data[data[metric].notna()].framework.unique()),'sessions':sorted(data.session_id.unique())})
    return True

def sustained_charts(output,selected_sessions):
    windows=pd.read_csv(ROOT/'results/processed/stress-windows.csv')
    if selected_sessions:windows=windows[windows.session_id.isin(selected_sessions)]
    resource=[]
    for path in (ROOT/'results/raw').glob('*/*/*/stress/metadata.json'):
        sid=path.parents[3].name
        if selected_sessions and sid not in selected_sessions:continue
        meta=json.loads(path.read_text())
        if meta.get('mode')!='research' or not meta.get('finished_at'):continue
        try:
            summary=json.loads(path.with_name('k6-summary.json').read_text())
            window=summary.get('benchmark_window')
            if not window:continue
            samples=pd.read_csv(path.with_name('docker-stats.csv'))
            samples=samples[(samples.role=='backend')&(samples.memory_mb>0)].copy()
            if meta.get('throughput_window')=='scenario-monotonic-v2':
                origin=pd.Timestamp(meta['monitor_started_at']).timestamp()
                elapsed=samples.elapsed_seconds-(float(window['started_at_ms'])/1000-origin)
            else:elapsed=pd.to_datetime(samples.timestamp,utc=True).astype('int64')/1e9-float(window['started_at_ms'])/1000
            samples['minute']=(elapsed//60).astype(int)+1
            samples=samples[(elapsed>=0)&(elapsed<=float(window['duration_seconds']))]
            for minute,group in samples.groupby('minute'):
                resource.append(dict(session_id=sid,framework=meta['framework'],profile=meta['profile'],scenario=meta['scenario'],minute=minute,cpu_avg_percent=group.cpu_percent.mean(),ram_avg_mb=group.memory_mb.mean()))
        except (OSError,ValueError,KeyError):continue
    data=pd.DataFrame(resource,columns=['session_id','framework','profile','scenario','minute','cpu_avg_percent','ram_avg_mb'])
    data=data.merge(windows.rename(columns={'p99_ms':'latency_p99_ms','rps':'achieved_rps'}),on=['session_id','framework','profile','scenario','minute'],how='outer')
    # A few final drain milliseconds are retained in CSVs, but are not a full
    # minute and must not create a misleading spike in a sustained-load curve.
    data=data[(data.window_seconds.isna())|(data.window_seconds==60)]
    data['elapsed_minutes']=data['minute']-.5
    for scenario in ['plaintext','json','json-large','compute','db-read','db-list','db-create','db-update','mixed']:
        frame=data[data.scenario==scenario]
        folder=output/'sustained'/scenario;folder.mkdir(parents=True,exist_ok=True)
        for name,field,label in [('p99','latency_p99_ms','Per-minute p99 latency (ms)'),('throughput','achieved_rps','Per-minute achieved RPS'),('cpu','cpu_avg_percent','Per-minute average CPU%'),('memory','ram_avg_mb','Per-minute average RAM (MiB)')]:
            chart(folder/(name+'.png'),frame,field,label,curve=True,x='elapsed_minutes',title=scenario+' — ten-minute sustained '+name)

def status_chart(path,summary,quick):
    if summary.empty:remove_placeholder(path);return False
    panels=panel_groups(summary);columns=min(3,len(panels));rows=math.ceil(len(panels)/columns)
    fig,axes=plt.subplots(rows,columns,figsize=(6.5*columns,4.6*rows),squeeze=False,layout='constrained')
    for ax,(key,frame) in zip(axes.flat,panels):
        if frame.empty:
            ax.text(.5,.5,'No measured load levels',ha='center',va='center',transform=ax.transAxes);ax.axis('off');continue
        targets=sorted(frame.target_rps.unique())
        for y,framework in enumerate(ORDER):
            for column,target in enumerate(targets):
                point=frame[(frame.framework==framework)&(frame.target_rps==target)]
                if point.empty:ax.text(column,y,'—',ha='center',va='center',color='gray')
                else:
                    for _,row in point.iterrows():
                        marker=STATES.get(point_state(row),STATES['BENCHMARK_ERROR'])[0]
                        ax.scatter(column,y,marker=marker,color=COLORS[framework],s=70)
                        warmups=f'; W{int(row.warmup_failures)}' if pd.notna(row.get('warmup_failures')) and row.get('warmup_failures',0)>0 else ''
                        ax.annotate(f'{int(row.repetitions)}/{int(row.required_repetitions)}'+warmups,(column,y),xytext=(9,-3),textcoords='offset points',fontsize=7)
        ax.set_xticks(range(len(targets)),[f'{int(n)}' for n in targets]);ax.set_xlabel('Target RPS; — = not measured')
        ax.set_yticks(range(len(ORDER)),ORDER);ax.set_ylim(len(ORDER)-.5,-.5);ax.set_xlim(-.5,len(targets)-.35)
        ax.set_title(f'{key[1]} / {key[2]}\nSession {key[0]}',fontsize=9);ax.grid(alpha=.15)
    for ax in list(axes.flat)[len(panels):]:ax.set_visible(False)
    observed={point_state(row) for _,row in summary.iterrows()}
    handles=[Line2D([0],[0],marker=marker,color='black',linestyle='none',label=('Observed pass (debug)' if state=='PASS' and quick else label)) for state,(marker,label) in STATES.items() if state in observed]
    if handles:fig.legend(handles=handles,loc='outside lower center',ncol=min(4,len(handles)),fontsize=8)
    fig.suptitle(('DEBUG ONLY — ' if quick else '')+'Every attempted load level\nCounts = measured / required repetitions; W = failed warm-ups',fontsize=12)
    fig.savefig(path,dpi=140);plt.close(fig)
    ARTIFACTS.append({'path':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),'metric':'states','numeric_points':len(summary),'frameworks':sorted(summary.framework.unique()),'sessions':sorted(summary.session_id.unique())})
    return True

def create(output=ROOT/'reports/charts', quick=False):
    ARTIFACTS.clear()
    output.mkdir(parents=True,exist_ok=True)
    processed=ROOT/'results/processed'
    summary=pd.read_csv(processed/('quick-summary.csv' if quick else 'summary.csv'))
    campaign=ROOT/'verification/research-campaign.json'
    selected_sessions=set(json.loads(campaign.read_text()).get('sessions',{}).values()) if campaign.exists() and not quick else set()
    if selected_sessions:summary=summary[summary.session_id.isin(selected_sessions)]
    warmups=pd.read_csv(processed/'warmup-failures.csv')
    if selected_sessions:warmups=warmups[warmups.session_id.isin(selected_sessions)]
    if quick:warmups=warmups.iloc[:0]
    states=with_warmup_failures(summary,warmups)
    maximum=sustainable(summary);prefix='DEBUG ONLY — ' if quick else ''
    selected=summary.merge(maximum[['session_id','framework','image_id','profile','scenario','max_sustainable_rps']],on=['session_id','framework','image_id','profile','scenario']) if not maximum.empty else pd.DataFrame()
    if not selected.empty:selected=selected[selected.target_rps==selected.max_sustainable_rps]
    chart(output/'max-rps.png',selected,'max_sustainable_rps','Highest passing target RPS',reference=summary,title=prefix+'Highest passing measured load (see all-load-levels for failures)')
    for name,field,label in [
        ('p95-latency','latency_p95_ms','p95 latency (ms)'),('p99-latency','latency_p99_ms','p99 latency (ms)'),
        ('average-cpu','cpu_avg_percent','Average CPU% (100% = one CPU)'),('peak-cpu','cpu_peak_percent','Peak CPU%'),
        ('average-memory','ram_avg_mb','Average memory (MiB)'),('peak-memory','ram_peak_mb','Peak memory (MiB)'),
        ('rps-per-vcpu','rps_per_vcpu','Achieved RPS / allocated vCPU'),('rps-per-gb','rps_per_avg_gb','Achieved RPS / average memory (GiB)')]:
        chart(output/(name+'.png'),summary,field,label,curve=True,x='target_rps',title=prefix+name.replace('-',' ').title()+' — all measured loads; median and min–max range')
    curves=[('throughput-vs-p99','latency_p99_ms','p99 latency (ms)'),('throughput-vs-cpu','cpu_avg_percent','Average CPU% (100% = one CPU)'),('throughput-vs-memory','ram_avg_mb','Average memory (MiB)')]
    for name,field,label in curves:chart(output/(name+'.png'),summary,field,label,curve=True,title=prefix+name.replace('-',' ').title()+' — median and min–max range')
    status_chart(output/'all-load-levels.png',states,quick)
    for session,frame in summary.groupby('session_id'):
        folder=output/'sessions'/session;folder.mkdir(parents=True,exist_ok=True)
        for name,field,label in curves:chart(folder/(name+'.png'),frame,field,label,curve=True,title=prefix+name.replace('-',' ').title())
        status_chart(folder/'all-load-levels.png',states[states.session_id==session],quick)
    # Readable workload figures keep all frameworks visible without a giant report image.
    for scenario,frame in summary.groupby('scenario'):
        folder=output/'workloads'/scenario;folder.mkdir(parents=True,exist_ok=True)
        for name,field,label in curves:chart(folder/(name+'.png'),frame,field,label,curve=True,title=prefix+scenario+' — '+name.replace('-',' ').title())
        status_chart(folder/'all-load-levels.png',states[states.scenario==scenario],quick)
    scaling=maximum.copy()
    if not scaling.empty:scaling['cpu']=scaling.profile.map({'small':1,'standard':2,'large':4});scaling['profile']='all profiles'
    if not quick and (processed/'scaling.csv').exists():
        compatible=pd.read_csv(processed/'scaling.csv');points=[]
        for _,row in compatible.iterrows():
            base=dict(profile='all profiles',scenario=row.scenario,framework=row.framework,session_id=row.comparison_id)
            points.extend([{**base,'cpu':1,'max_sustainable_rps':row.baseline_rps,'ram_avg_mb':row.baseline_ram_avg_mb},{**base,'cpu':row.allocated_cpu,'max_sustainable_rps':row.scaled_rps,'ram_avg_mb':row.ram_avg_mb}])
        if points:scaling=pd.DataFrame(points).drop_duplicates(['scenario','framework','session_id','cpu'])
    if not scaling.empty:
        scaling=scaling.groupby(['session_id','framework','scenario']).filter(lambda group:group.cpu.nunique()>=2)
    chart(output/'cpu-scaling.png',scaling,'max_sustainable_rps','Maximum sustainable RPS',curve=True,x='cpu',title=prefix+'CPU scaling')
    chart(output/'memory-scaling.png',scaling,'ram_avg_mb','Average memory (MiB)',curve=True,x='cpu',title=prefix+'Memory scaling')
    startup=pd.read_csv(processed/'startup.csv');startup=startup[startup['mode']==('quick' if quick else 'research')].copy();startup['scenario']='startup'
    if selected_sessions:startup=startup[startup.session_id.isin(selected_sessions)]
    chart(output/'startup-time.png',startup,'startup_median_ms','Median startup time (ms)',title=prefix+'Startup time')
    chart(output/'docker-image-size.png',startup,'image_size_mb','Local image size (MiB), uncompressed',title=prefix+'Local image size')
    if not quick:sustained_charts(output,selected_sessions)
    (output/'artifacts.json').write_text(json.dumps(ARTIFACTS,indent=2)+'\n')
    print('Charts: '+str(output))

if __name__=='__main__':create()
