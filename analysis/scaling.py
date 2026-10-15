"""Compare profile sessions only when host, versions and methodology match."""
import hashlib
import json
import pandas as pd
from process_results import ROOT

def calculate(maximum):
    cols=['comparison_id','framework','scenario','baseline_session','scaled_session','allocated_cpu','baseline_rps','scaled_rps','speedup','scaling_efficiency','baseline_ram_avg_mb','ram_avg_mb']
    rows=[]
    if maximum.empty:
        return pd.DataFrame(columns=cols)
    data=maximum.copy()
    signatures={}
    for sid in data.session_id.unique():
        path=ROOT/'results/raw'/sid
        host=json.loads((path/'host.json').read_text())
        versions=json.loads((path/'versions.json').read_text())
        session=json.loads((path/'session.json').read_text())
        native=host.get('native_host',{})
        stable_host={k:host.get(k) for k in ['os','kernel','architecture','logical_cpus','memory_gb','docker_version','docker_compose_version']}
        stable_host.update({k:native.get(k) for k in ['cpu_model','logical_cpus','memory_gb','architecture','os'] if native.get(k) is not None})
        # Image digests are meaningful for compiled applications; resource overrides are per profile.
        # Image size can be shared by both profiles; avoid volatile version fields.
        signature={'host':stable_host,'versions':versions,'config':session['config'],'fixture_sha256':session['fixture_sha256']}
        signatures[sid]=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:16]
    data['comparison_id']=data.session_id.map(signatures)
    for (signature,framework,scenario),group in data.groupby(['comparison_id','framework','scenario']):
        baseline=group[group.profile=='small'].sort_values('session_id')
        if baseline.empty:continue
        base=baseline.iloc[-1]
        for profile,cpu in [('standard',2),('large',4)]:
            scaled=group[group.profile==profile].sort_values('session_id')
            if scaled.empty:continue
            item=scaled.iloc[-1]
            speed=item.max_sustainable_rps/base.max_sustainable_rps
            rows.append(dict(comparison_id=signature,framework=framework,scenario=scenario,baseline_session=base.session_id,scaled_session=item.session_id,allocated_cpu=cpu,baseline_rps=base.max_sustainable_rps,scaled_rps=item.max_sustainable_rps,speedup=speed,scaling_efficiency=speed/cpu,baseline_ram_avg_mb=base.ram_avg_mb,ram_avg_mb=item.ram_avg_mb))
    return pd.DataFrame(rows,columns=cols)
