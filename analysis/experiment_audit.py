"""Coverage and provenance derived from recorded measurements, never inferred runs."""
import hashlib
import json
import pandas as pd
from process_results import ROOT

FRAMEWORKS=['springboot','fastapi','laravel','express','fastify','aspnet','fiber','axum']
SCENARIOS=['plaintext','json','json-large','compute','db-read','db-list','db-create','db-update','mixed']

def coverage(data,planned=()):
    columns=['mode','profile','scenario','framework','normal_runs','stress_runs','valid_runs','passing_runs','failed_runs','rejected_runs','target_levels','sessions','status']
    rows=[]
    combinations=set(data[['mode','profile']].dropna().itertuples(index=False,name=None))|set(planned)
    for mode,profile in sorted(combinations):
        group=data[(data['mode']==mode)&(data.profile==profile)]
        for scenario in SCENARIOS:
            for framework in FRAMEWORKS:
                measured=group[(group.scenario==scenario)&(group.framework==framework)]
                normal=measured[measured.kind=='normal']
                status=('PENDING' if (mode,profile) in planned else 'NOT_RUN') if measured.empty else 'MEASURED'
                if not measured.empty and measured.rejected.all():status='REJECTED'
                elif not normal.empty and not normal.sustainable.any():status='NO_PASSING_LEVEL'
                rows.append(dict(mode=mode,profile=profile,scenario=scenario,framework=framework,
                    normal_runs=len(normal),stress_runs=int((measured.kind=='stress').sum()),
                    valid_runs=int((measured.rejected==False).sum()),passing_runs=int(measured.sustainable.sum()),
                    failed_runs=int(((measured.rejected==False)&(measured.sustainable==False)).sum()),
                    rejected_runs=int(measured.rejected.sum()),target_levels=','.join(str(int(x)) for x in sorted(normal.target_rps.dropna().unique())),
                    sessions=','.join(sorted(measured.session_id.unique())),status=status))
    return pd.DataFrame(rows,columns=columns)

def provenance(data,root=ROOT):
    columns=['session_id','mode','profile','framework','image_id','framework_version','runtime_version','application_source_sha256','harness_source_sha256','fixture_sha256','normal_runs','stress_runs','purpose','note']
    rows=[]
    for session_id,group in data.groupby('session_id'):
        session=root/'results/raw'/session_id
        settings=json.loads((session/'session.json').read_text())
        versions=json.loads((session/'versions.json').read_text())
        manifest_path=session/'source-manifest.json'
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        completed=session/'completed.json'
        purpose=json.loads(completed.read_text()).get('purpose','Benchmark diagnostics' if settings['mode']=='quick' else 'Research benchmark') if completed.exists() else 'Incomplete session'
        notes=session/'notes.json'
        note=json.loads(notes.read_text(encoding='utf-8-sig')).get('note','') if notes.exists() else ''
        def digest(files):
            return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest() if files else 'not recorded'
        harness=digest({k:v for k,v in manifest.items() if not k.startswith('apps/')})
        for framework,measured in group.groupby('framework'):
            rows.append(dict(session_id=session_id,mode=settings['mode'],profile=settings['profile'],framework=framework,
                image_id=versions.get('resolved_images',{}).get(framework,{}).get('id','unknown'),
                framework_version=versions[framework]['framework'],runtime_version=versions[framework]['runtime'],
                application_source_sha256=digest({k:v for k,v in manifest.items() if k.startswith('apps/'+framework+'/')}),
                harness_source_sha256=harness,fixture_sha256=settings.get('fixture_sha256','unknown'),
                normal_runs=int((measured.kind=='normal').sum()),stress_runs=int((measured.kind=='stress').sum()),purpose=purpose,note=note))
    return pd.DataFrame(rows,columns=columns)

def create(data,root=ROOT):
    output=root/'results/processed'
    campaign=root/'verification/research-campaign.json'
    planned=[('research',profile) for profile in json.loads(campaign.read_text()).get('profiles',[])] if campaign.exists() else []
    selected=set(json.loads(campaign.read_text()).get('sessions',{}).values()) if campaign.exists() else set()
    eligible=data[(data['mode']!='research')|data.session_id.isin(selected)] if selected else data
    coverage(eligible,planned).to_csv(output/'coverage.csv',index=False)
    provenance(data,root).to_csv(output/'provenance.csv',index=False)

if __name__=='__main__':create(pd.read_csv(ROOT/'results/processed/all-runs.csv'))
