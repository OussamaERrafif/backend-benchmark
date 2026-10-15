"""Docker-only controller. Host launchers need Docker and Git, not Python."""
import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path('/workspace') if Path('/workspace').exists() else Path(__file__).resolve().parents[1]
FRAMEWORKS = ['springboot','fastapi','laravel','express','fastify','aspnet','fiber','axum']
SCENARIOS = ['plaintext','json','json-large','compute','db-read','db-list','db-create','db-update','mixed']
PROFILES = {
    'small': (1,'512m',1,'1g',1,'512m'),
    'standard': (2,'2g',1,'1g',2,'1g'),
    'large': (4,'4g',2,'2g',2,'1g'),
}

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n')

def command(args, check=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if check and result.returncode:
        raise RuntimeError(f'{" ".join(args)} failed ({result.returncode}):\n{result.stderr}\n{result.stdout}')
    return result

def docker(*args, check=True): return command(['docker', *map(str,args)], check=check)

def duration(value):
    import re
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(s|m|h)', value)
    if not match: raise ValueError(f'Invalid duration: {value}')
    return float(match[1]) * {'s':1,'m':60,'h':3600}[match[2]]

def configuration():
    config = {}
    env_path = ROOT / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=',1)
                config[key.strip()] = value.strip().strip('"\'')
    # Host wrapper passes only benchmark-specific environment overrides.
    config.update({k:v for k,v in os.environ.items() if k in {
        'BENCH_MODE','REPETITIONS','WARMUP_DURATION','BENCH_DURATION','RPS_STEPS','SCENARIOS',
        'MAX_ERROR_RATE','MAX_P95_MS','MAX_P99_MS','MIN_TARGET_ACHIEVEMENT','DB_POOL_SIZE',
        'RANDOM_SEED','COOLDOWN_SECONDS','STRESS_DURATION','RUN_STRESS','PREALLOCATED_VUS','MAX_VUS',
        'POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD'}})
    defaults = dict(BENCH_MODE='research', REPETITIONS='3', WARMUP_DURATION='60s', BENCH_DURATION='120s',
        RPS_STEPS='50,100,250,500,1000,2000,4000,8000', SCENARIOS=','.join(SCENARIOS),
        MAX_ERROR_RATE='0.01', MAX_P95_MS='100', MAX_P99_MS='250', MIN_TARGET_ACHIEVEMENT='0.98',
        DB_POOL_SIZE='20', RANDOM_SEED='20260930', COOLDOWN_SECONDS='30', STRESS_DURATION='600s',
        RUN_STRESS='1', PREALLOCATED_VUS='100', MAX_VUS='2000', POSTGRES_DB='benchmark',
        POSTGRES_USER='benchmark', POSTGRES_PASSWORD='benchmark')
    defaults.update(config)
    if defaults['BENCH_MODE'] == 'quick':
        defaults.update(REPETITIONS='1',WARMUP_DURATION='10s',BENCH_DURATION='20s',RPS_STEPS='100,500,1000',RUN_STRESS='0',COOLDOWN_SECONDS='0')
    elif defaults['BENCH_MODE'] != 'research': raise ValueError('BENCH_MODE must be quick or research')
    elif int(defaults['REPETITIONS']) < 3 or duration(defaults['WARMUP_DURATION']) < 60 or duration(defaults['BENCH_DURATION']) < 120:
        raise ValueError('Research requires >=3 repetitions, >=60s warm-up, >=120s measurement')
    if int(defaults['DB_POOL_SIZE']) < 4: raise ValueError('DB_POOL_SIZE must be at least 4')
    if not 0<float(defaults['MAX_ERROR_RATE'])<=1 or not 0<float(defaults['MIN_TARGET_ACHIEVEMENT'])<=1:
        raise ValueError('Error and target-achievement thresholds must be in (0,1]')
    if float(defaults['MAX_P95_MS'])<=0 or float(defaults['MAX_P99_MS'])<=0:raise ValueError('Latency thresholds must be positive')
    if defaults['BENCH_MODE']=='research' and defaults['RUN_STRESS']=='1' and duration(defaults['STRESS_DURATION'])<600:
        raise ValueError('Research sustained load requires at least 600 seconds')
    if int(defaults['PREALLOCATED_VUS']) < 1 or int(defaults['MAX_VUS']) < int(defaults['PREALLOCATED_VUS']): raise ValueError('Invalid VU budget')
    steps = [int(x) for x in defaults['RPS_STEPS'].split(',')]
    if not steps or any(n<=0 for n in steps) or steps != sorted(set(steps)): raise ValueError('RPS_STEPS must be increasing positive integers')
    if not set(defaults['SCENARIOS'].split(',')) <= set(SCENARIOS): raise ValueError('Unknown scenario')
    return defaults

class Controller:
    def __init__(self, profile='small'):
        self.profile = profile
        self.config = configuration()
        cpu,ram,pgcpu,pgram,kcpu,kram = PROFILES[profile]
        self.cpu, self.ram_mb, self.pgcpu, self.kcpu = cpu, {'512m':512,'2g':2048,'4g':4096}[ram], pgcpu, kcpu
        env = os.environ.copy(); env.update(self.config)
        env.update(BACKEND_CPU=str(cpu),BACKEND_RAM=ram,POSTGRES_CPU=str(pgcpu),POSTGRES_RAM=pgram,K6_CPU=str(kcpu),K6_RAM=kram,APP_WORKERS=str(cpu))
        raw = command(['docker','compose','--profile','*','-f',str(ROOT/'compose.yml'),'config','--format','json'], env=env).stdout
        spec = json.loads(raw)
        # Bind sources must refer to the daemon host, not this controller's /workspace.
        own = json.loads(docker('inspect',os.environ['HOSTNAME']).stdout)[0]
        host_root = next(m['Source'] for m in own['Mounts'] if m['Destination']=='/workspace')
        for service in spec['services'].values():
            for volume in service.get('volumes',[]):
                if volume.get('type')=='bind' and volume['source'].startswith(str(ROOT)):
                    volume['source'] = host_root + volume['source'][len(str(ROOT)):]
        self.compose_file = Path('/tmp') / f'benchmark-compose-{uuid.uuid4().hex}.json'
        self.compose_file.write_text(json.dumps(spec))
        self.base = ['docker','compose','--project-directory',str(ROOT),'-f',str(self.compose_file)]
        self.spec = spec

    def compose(self,*args,check=True): return command(self.base+list(map(str,args)),check=check)
    def container(self, service):
        ids = self.compose('ps','-a','-q',service).stdout.strip().splitlines()
        return ids[0] if ids else None
    def inspect(self, id): return json.loads(docker('inspect',id).stdout)[0]
    def info(self): return json.loads(docker('info','--format','{{json .}}').stdout)

    def doctor(self):
        info = self.info()
        if info['OSType']!='linux': raise RuntimeError('Switch Docker Desktop to Linux containers')
        self.compose('config','--quiet')
        required = ['compose.yml','versions.lock','fixtures/large.json','database/schema.sql','database/seed.sql','database/reset.sql']
        required += [f'apps/{f}/Dockerfile' for f in FRAMEWORKS] + [f'load-tests/{s}.js' for s in SCENARIOS]
        missing = [p for p in required if not (ROOT/p).is_file()]
        if missing: raise RuntimeError('Missing files: '+','.join(missing))
        free_gb = shutil.disk_usage(ROOT).free / 2**30
        if free_gb < 5: raise RuntimeError(f'Only {free_gb:.1f} GB disk free; need at least 5 GB (20+ recommended)')
        budget = self.cpu+self.pgcpu+self.kcpu
        ram_need = self.ram_mb + (2048 if self.profile=='large' else 1024) + (512 if self.profile=='small' else 1024) + 1024
        if info['NCPU'] < budget or info['MemTotal']/2**20 < ram_need:
            raise RuntimeError(f'{self.profile} would overcommit Docker: need {budget} CPUs and {ram_need} MB including overhead; choose small or increase Docker resources')
        occupied=[]
        for cid in docker('ps','-q').stdout.split():
            item=self.inspect(cid)
            if item['Config'].get('Labels',{}).get('com.docker.compose.project')=='backend-benchmark': continue
            for bindings in (item['NetworkSettings'].get('Ports') or {}).values():
                if bindings and any(b['HostPort']=='8080' for b in bindings): occupied.append(item['Name'])
        if occupied: raise RuntimeError('Port 8080 is used by other containers: '+','.join(occupied))
        print(f'Docker ............... OK ({info["ServerVersion"]}, {info["OperatingSystem"]})\nDocker Compose ....... OK\nCPU .................. {info["NCPU"]} logical CPUs\nRAM .................. {info["MemTotal"]/2**30:.1f} GB allocated to Docker\nDisk ................. {free_gb:.1f} GB available\nRequired files ....... OK\nContainer ports ...... OK\nBenchmark profile .... {self.profile} supported\nREADY',flush=True)
        return info

    def stop_backends(self):
        self.compose('stop',*FRAMEWORKS,check=False)

    def database(self, reset=False):
        self.compose('up','-d','--no-build','--wait','--wait-timeout','120','postgres')
        if reset:
            self.compose('exec','-T','postgres','psql','-v','ON_ERROR_STOP=1','-U',self.config['POSTGRES_USER'],'-d',self.config['POSTGRES_DB'],'-f','/database/reset.sql')
        count = self.compose('exec','-T','postgres','psql','-At','-U',self.config['POSTGRES_USER'],'-d',self.config['POSTGRES_DB'],'-c','SELECT COUNT(*) FROM users').stdout.strip()
        if reset and count!='100000': raise RuntimeError('Database reset failed: '+count)
        if int(count)<100000: raise RuntimeError('Database initialization is incomplete: '+count)

    def request(self, framework, path, method='GET', data=None):
        body=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(f'http://{framework}:8080{path}',body,{'Content-Type':'application/json'},method=method)
        try: response=urllib.request.urlopen(req,timeout=15)
        except urllib.error.HTTPError as e: response=e
        with response:
            content=response.read().decode()
            return response.status, response.headers.get('Content-Type',''), content

    def wait_health(self, framework):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                status,_,body=self.request(framework,'/health')
                if status==200 and json.loads(body)=={'status':'ok'}: return
            except (OSError,ValueError): pass
            time.sleep(.2)
        raise RuntimeError(framework+' never became healthy\n'+self.compose('logs','--tail','100',framework,check=False).stdout)

    def start(self, framework):
        self.stop_backends()
        self.compose('up','-d','--no-build',framework)
        self.wait_health(framework)
        item=self.inspect(self.container(framework))
        host=item['HostConfig']
        expected=self.ram_mb*2**20
        if host['NanoCpus']!=self.cpu*10**9 or host['Memory']!=expected or host['MemorySwap']!=expected:
            raise RuntimeError('Docker did not enforce CPU, RAM, or swap limits')

    def smoke(self, framework, output=None):
        evidence=[]
        def expect(path,expected,code=200,method='GET',data=None,plain=False):
            status,kind,text=self.request(framework,path,method,data)
            actual=text if plain else json.loads(text)
            assert status==code, (framework,path,status,code,text)
            assert actual==expected, (framework,path,actual,expected)
            assert ('text/plain' if plain else 'application/json') in kind, (path,kind)
            evidence.append({'method':method,'path':path,'status':status,'content_type':kind,'body_sha256':hashlib.sha256(text.encode()).hexdigest()})
            return actual
        expect('/health',{'status':'ok'})
        expect('/plaintext','Hello, World!',plain=True)
        expect('/json',{'message':'Hello, World!'})
        expect('/json-large',json.loads((ROOT/'fixtures/large.json').read_text()))
        a=0
        for i in range(1,100001): a=(a+(i*i)%97)%1000000007
        expect('/compute',{'result':a})
        expect('/users/50000',{'id':50000,'name':'User 50000','email':'user50000@example.test'})
        expect('/users?limit=100',[{'id':i,'name':f'User {i}','email':f'user{i}@example.test'} for i in range(1,101)])
        expect('/users?limit=1',[{'id':1,'name':'User 1','email':'user1@example.test'}])
        expect('/users?limit=101',{'error':'invalid request'},400)
        expect('/users?limit=0',{'error':'invalid request'},400)
        expect('/users?limit=-1',{'error':'invalid request'},400)
        expect('/users?limit=1.0',{'error':'invalid request'},400)
        expect('/users?limit=abc',{'error':'invalid request'},400)
        expect('/users/0',{'error':'invalid request'},400)
        expect('/users/nope',{'error':'invalid request'},400)
        expect('/users/999999999',{'error':'not found'},404)
        expect('/users',{'error':'invalid request'},400,'POST',{'name':''})
        created=expect('/users',{'id':100001,'name':'Benchmark User','email':'smoke@example.test'},201,'POST',{'name':'Benchmark User','email':'smoke@example.test'})
        expect('/users',{'error':'conflict'},409,'POST',{'name':'Duplicate','email':'smoke@example.test'})
        expect('/users/100001',{'id':100001,'name':'Updated User','email':'smoke@example.test'},200,'PUT',{'name':'Updated User'})
        expect('/users/999999999',{'error':'not found'},404,'PUT',{'name':'Updated User'})
        expect('/users/1',{'error':'invalid request'},400,'PUT',{'name':''})
        if output: save(output,{'framework':framework,'timestamp':utc(),'passed':True,'checks':evidence})
        print(f'{framework}: {len(evidence)} API contract checks PASS',flush=True)
        return evidence

    def smoke_all(self, session):
        failures=[]
        for framework in FRAMEWORKS:
            try:
                self.database(reset=True); self.start(framework)
                self.smoke(framework,session/framework/'smoke.json')
            except Exception as e:
                save(session/framework/'smoke.json',{'passed':False,'error':str(e),'timestamp':utc()})
                failures.append(framework+': '+str(e)); print(failures[-1],flush=True)
            finally: self.compose('stop',framework,check=False)
        self.database(reset=True)
        if failures: raise RuntimeError('Smoke gate failed:\n'+'\n'.join(failures))

    def session(self):
        path=ROOT/'results/raw'/(dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')+'_'+self.profile+'_'+uuid.uuid4().hex[:8])
        path.mkdir(parents=True,exist_ok=False)
        info=self.info()
        host={'date':utc(),'os':info['OperatingSystem'],'kernel':info['KernelVersion'],'architecture':info['Architecture'],'logical_cpus':info['NCPU'],'memory_gb':info['MemTotal']/2**30,'cpu_model':None,'docker_version':info['ServerVersion'],'docker_compose_version':docker('compose','version','--short').stdout.strip(),'docker_desktop':'Docker Desktop' in info['OperatingSystem'],'benchmark_git_commit':None,'note':'CPU/RAM describe Docker VM; native host details recorded separately by launchers where available.'}
        native=ROOT/'verification/host-native.json'
        if native.exists(): host['native_host']=json.loads(native.read_text(encoding='utf-8-sig'))
        try: host['benchmark_git_commit']=command(['git','-C',str(ROOT),'rev-parse','HEAD']).stdout.strip()
        except Exception: pass
        save(path/'host.json',host)
        versions=json.loads((ROOT/'versions.lock').read_text())
        versions['resolved_images']={}
        for service in FRAMEWORKS+['postgres','k6','analysis']:
            image=self.spec['services'][service]['image']
            result=docker('image','inspect',image,check=False)
            if result.returncode==0:
                image_info=json.loads(result.stdout)[0]
                version_environment={}
                for entry in image_info.get('Config',{}).get('Env',[]):
                    key,_,value=entry.partition('=')
                    if key in {'JAVA_VERSION','NODE_VERSION','PYTHON_VERSION','PHP_VERSION','ASPNET_VERSION','DOTNET_VERSION','GOLANG_VERSION','RUST_VERSION'}:version_environment[key]=value
                versions['resolved_images'][service]={'image':image,'id':image_info['Id'],'repo_digests':image_info.get('RepoDigests',[]),'local_size_bytes':image_info['Size'],'image_runtime_versions':version_environment}
        save(path/'versions.json',versions)
        # Preserve a source manifest even before the first Git commit exists.
        manifest={}
        for relative in ['apps','database','fixtures','load-tests','scripts','analysis']:
            for source in sorted((ROOT/relative).rglob('*')):
                if source.is_file() and not any(part in {'node_modules','vendor','target','obj','bin','__pycache__'} for part in source.parts):
                    manifest[source.relative_to(ROOT).as_posix()]=hashlib.sha256(source.read_bytes()).hexdigest()
        for source in ['compose.yml','versions.lock','.env.example']:
            manifest[source]=hashlib.sha256((ROOT/source).read_bytes()).hexdigest()
        save(path/'source-manifest.json',manifest)
        safe={k:v for k,v in self.config.items() if k!='POSTGRES_PASSWORD'}
        save(path/'session.json',{'created_at':utc(),'profile':self.profile,'config':safe,'mode':self.config['BENCH_MODE'],'frameworks':FRAMEWORKS,'fixture_sha256':hashlib.sha256((ROOT/'fixtures/large.json').read_bytes()).hexdigest()})
        return path

    def startup(self, framework, directory):
        measurements=[]
        for _ in range(5):
            self.compose('stop',framework)
            cid=self.container(framework)
            docker('start',cid); self.wait_health(framework)
            healthy_at=time.time()
            item=self.inspect(cid)
            started=dt.datetime.fromisoformat(item['State']['StartedAt'].replace('Z','+00:00')).timestamp()
            measurements.append((healthy_at-started)*1000)
        save(directory/'startup.json',{'milliseconds':measurements,'definition':'Docker StartedAt to first successful health response, 200ms polling','local_image_size_bytes':json.loads(docker('image','inspect',self.spec['services'][framework]['image']).stdout)[0]['Size'],'compressed_image_size_bytes':None})

    def load(self, framework, scenario, rate, seconds, directory, measured=True, repetition=1, kind='normal'):
        directory.mkdir(parents=True,exist_ok=False)
        token=uuid.uuid4().hex
        kname='benchmark-k6-'+token[:12]
        relative=directory.relative_to(ROOT/'results/raw').as_posix()
        args=['run','-d','--no-deps','--name',kname]
        variables=dict(BASE_URL=f'http://{framework}:8080',TARGET_RPS=str(rate),DURATION=seconds,
            SUMMARY_PATH=f'/results/{relative}/k6-summary.json',RUN_TOKEN=token,
            RANDOM_SEED=self.config['RANDOM_SEED'],PREALLOCATED_VUS=self.config['PREALLOCATED_VUS'],MAX_VUS=self.config['MAX_VUS'])
        for key,value in variables.items(): args+=['-e',f'{key}={value}']
        args+=['k6','run']
        if kind=='stress':args+=['--out',f'json=/results/{relative}/k6-timeseries.json.gz']
        args+=[f'/tests/{scenario}.js']
        metadata={'timestamp':utc(),'framework':framework,'profile':self.profile,'allocated_cpu':self.cpu,'allocated_ram_mb':self.ram_mb,'postgres_cpu':self.pgcpu,'k6_cpu':self.kcpu,'workers':self.cpu,'db_pool_max':self.cpu if framework=='laravel' else (int(self.config['DB_POOL_SIZE']) if framework not in ['express','fastify','fastapi'] else max(1,int(self.config['DB_POOL_SIZE'])//self.cpu)*self.cpu),'scenario':scenario,'target_rps':rate,'duration_seconds':duration(seconds),'repetition':repetition,'mode':self.config['BENCH_MODE'],'kind':kind,'random_seed':int(self.config['RANDOM_SEED']),'thresholds':{k:float(self.config[k]) for k in ['MAX_ERROR_RATE','MAX_P95_MS','MAX_P99_MS','MIN_TARGET_ACHIEVEMENT']},'k6_exit_code':None}
        save(directory/'metadata.json',metadata)
        metadata['workers']=self.cpu if framework in ['fastapi','express','fastify','laravel'] else 1
        metadata['runtime_cpu_budget']=self.cpu
        metadata['throughput_window']='scenario-monotonic-v2'
        metadata['k6_container_name']=kname
        save(directory/'metadata.json',metadata)
        ids={'backend':self.container(framework),'postgres':self.container('postgres')}
        stop=threading.Event(); monitor=None
        sample_origin=time.monotonic()
        metadata['monitor_started_at']=utc()
        save(directory/'metadata.json',metadata)
        try:
            self.compose(*args)
            ids['k6']=kname
            monitor=threading.Thread(target=sample_stats,args=(ids,directory/'docker-stats.csv',stop,sample_origin),daemon=True)
            monitor.start()
            wait=docker('wait',kname,check=False)
            # End resource sampling with k6, before logs and recovery health checks.
            stop.set();monitor.join(timeout=10)
            metadata['k6_exit_code']=int(wait.stdout.strip()) if wait.returncode==0 else -1
            metadata['k6_state']=self.inspect(kname)['State']
            (directory/'k6.log').write_text(docker('logs',kname,check=False).stdout+docker('logs',kname,check=False).stderr)
            metadata['backend_state']=self.inspect(ids['backend'])['State']
            metadata['postgres_state']=self.inspect(ids['postgres'])['State']
            try: self.wait_health(framework); metadata['backend_healthy']=True
            except Exception: metadata['backend_healthy']=False
        except Exception as e:
            metadata['execution_error']=str(e)
        finally:
            stop.set()
            if monitor: monitor.join(timeout=10)
            if measured:
                for role,cid in ids.items():
                    logs=docker('logs','--tail','100',cid,check=False)
                    (directory/f'{role}.log').write_text(logs.stdout+logs.stderr)
            docker('rm','-f',kname,check=False)
            metadata['finished_at']=utc()
            save(directory/'metadata.json',metadata)
        if not measured:
            if metadata.get('k6_exit_code') not in [0,99] or not metadata.get('backend_healthy'):
                raise RuntimeError('Warm-up infrastructure failed; inspect '+str(directory))
            return None
        sys.path.insert(0,str(ROOT/'analysis'))
        from process_results import parse_run
        row=parse_run(directory)
        save(directory/'validation.json',{'state':row['state'],'rejected':row['rejected'],'reason':row.get('reason','')})
        print(f'{framework} {scenario} {rate} RPS run {repetition}: {row["state"]}',flush=True)
        return row

    def benchmark(self, frameworks, session):
        # Every implementation must pass the same gate before collecting measurements.
        save(session/'progress.json',{'timestamp':utc(),'state':'PREFLIGHT','completed_measurements':0})
        self.smoke_all(session)
        for framework in frameworks:
            save(session/'progress.json',{'timestamp':utc(),'state':'STARTUP','framework':framework,'completed_measurements':0})
            process_workers=self.cpu if framework in ['fastapi','express','fastify','laravel'] else 1
            save(session/framework/'metadata.json',{'framework':framework,'profile':self.profile,'workers':process_workers,'runtime_cpu_budget':self.cpu,'allocated_cpu':self.cpu,'allocated_ram_mb':self.ram_mb,'pool':json.loads((ROOT/'versions.lock').read_text())[framework]['pool']})
            self.database(reset=True);self.start(framework)
            if not (session/framework/'startup.json').exists():self.startup(framework,session/framework)
            self.compose('stop',framework)
        order=[];rows=[]
        command(['python',str(ROOT/'analysis/pipeline.py')])
        try:
            for scenario in self.config['SCENARIOS'].split(','):
                active=frameworks.copy();saturated={framework:0 for framework in frameworks}
                for rate in map(int,self.config['RPS_STEPS'].split(',')):
                    level={framework:[] for framework in active}
                    for repetition in range(1,int(self.config['REPETITIONS'])+1):
                        current=active.copy();random.Random(int(self.config['RANDOM_SEED'])+repetition).shuffle(current)
                        order.append({'scenario':scenario,'target_rps':rate,'repetition':repetition,'frameworks':current});save(session/'execution-order.json',order)
                        for framework in current:
                            save(session/'progress.json',{'timestamp':utc(),'state':'RUNNING','framework':framework,'scenario':scenario,'target_rps':rate,'repetition':repetition,'completed_attempts':len(rows),'completed_measurements':sum(r.get('kind')!='warmup_failure' for r in rows)})
                            row,fresh=self.collect_repetition(framework,scenario,rate,repetition,session)
                            rows.append(row);level[framework].append(row)
                            if row.get('rejected') and row['state']=='BENCHMARK_ERROR':raise RuntimeError('Invalid measurement requires investigation: '+row.get('source','')+' '+row.get('reason',''))
                            if fresh and int(self.config['COOLDOWN_SECONDS']):time.sleep(int(self.config['COOLDOWN_SECONDS']))
                    # Every visited level gets every repetition before an adaptive stop.
                    for framework,outcomes in level.items():
                        passing=all(row['sustainable'] for row in outcomes)
                        saturated[framework]=0 if passing else saturated[framework]+1
                        critical=any(row['state'] in ['BACKEND_CRASH','OUT_OF_MEMORY','HEALTH_CHECK_FAILED'] for row in outcomes)
                        # A harness OOM is not a backend failure, but higher loads cannot be measured either.
                        harness=any(row['state'] in ['LOAD_GENERATOR_OOM','DATABASE_OOM'] for row in outcomes)
                        if critical or harness or saturated[framework]>=2:
                            active.remove(framework)
                            rule='Critical backend container/health failure' if critical else 'Load generator or database ran out of memory; backend capacity unresolved' if harness else 'Two consecutive levels failed required repetitions'
                            save(session/framework/scenario/'load-boundary.json',{'timestamp':utc(),'target_rps':rate,'states':[row['state'] for row in outcomes],'rule':rule,'higher_loads_skipped':True})
                    command(['python',str(ROOT/'analysis/pipeline.py')])
                    if not active:break
            if self.config['RUN_STRESS']=='1' and self.config['BENCH_MODE']=='research':
                for framework in frameworks:
                    self.start(framework)
                    try:
                        for scenario in self.config['SCENARIOS'].split(','):
                            passing=[r for r in rows if r['framework']==framework and r['scenario']==scenario and r['sustainable']]
                            candidates=[r['target_rps'] for r in passing if sum(x['target_rps']==r['target_rps'] for x in passing)>=int(self.config['REPETITIONS'])]
                            if not candidates:
                                save(session/framework/scenario/'stress-skipped.json',{'reason':'No load level passed every required repetition'});continue
                            rate=max(1,math.floor(.8*max(candidates)));self.database(reset=True)
                            stress_dir=session/framework/scenario/'stress'
                            if stress_dir.exists() and json.loads((stress_dir/'metadata.json').read_text()).get('finished_at'):continue
                            if stress_dir.exists():
                                interrupted=stress_dir.with_name('aborted-stress-'+uuid.uuid4().hex[:8]);stress_dir.rename(interrupted)
                                save(interrupted/'interrupted.json',{'timestamp':utc(),'reason':'Controller stopped before the stress measurement finished'})
                            warmup=session/framework/scenario/('stress-warmup-'+uuid.uuid4().hex[:8])
                            save(session/'progress.json',{'timestamp':utc(),'state':'STRESS','framework':framework,'scenario':scenario,'target_rps':rate,'completed_measurements':len(rows)})
                            self.load(framework,scenario,rate,self.config['WARMUP_DURATION'],warmup,measured=False)
                            if scenario in ['db-create','db-update','mixed']:self.database(reset=True)
                            self.load(framework,scenario,rate,self.config['STRESS_DURATION'],stress_dir,kind='stress')
                    finally:self.compose('stop',framework,check=False);self.database(reset=True)
        finally:
            self.stop_backends()
            command(['python',str(ROOT/'analysis/pipeline.py')])

    def collect_repetition(self,framework,scenario,rate,repetition,session):
        sys.path.insert(0,str(ROOT/'analysis'))
        from process_results import parse_run, oom_state
        run_dir=session/framework/scenario/f'{rate}-rps'/f'run-{repetition}'
        failure=run_dir.parent/f'warmup-failed-{repetition}.json'
        if failure.exists():return {**json.loads(failure.read_text()),'kind':'warmup_failure','sustainable':False},False
        if run_dir.exists():
            existing=json.loads((run_dir/'metadata.json').read_text()) if (run_dir/'metadata.json').exists() else {}
            if existing.get('finished_at'):return parse_run(run_dir),False
            interrupted=run_dir.with_name('aborted-'+run_dir.name+'-'+uuid.uuid4().hex[:8])
            if not interrupted.resolve().is_relative_to(session.resolve()):raise RuntimeError('Invalid interruption archive path')
            run_dir.rename(interrupted)
            save(interrupted/'interrupted.json',{'timestamp':utc(),'reason':'Controller stopped before this measurement finished; preserved separately from completed repetitions'})
        self.database(reset=True);self.start(framework)
        try:
            warmup=run_dir.parent/f'warmup-{repetition}'
            if warmup.exists():warmup=warmup.with_name(warmup.name+'-'+uuid.uuid4().hex[:8])
            try:self.load(framework,scenario,rate,self.config['WARMUP_DURATION'],warmup,measured=False,repetition=repetition)
            except RuntimeError as error:
                evidence=json.loads((warmup/'metadata.json').read_text()) if (warmup/'metadata.json').exists() else {}
                backend=evidence.get('backend_state',{})
                state=oom_state(evidence) or ('BACKEND_CRASH' if backend and not backend.get('Running') else 'HEALTH_CHECK_FAILED')
                row={'timestamp':utc(),'session_id':session.name,'framework':framework,'profile':self.profile,'scenario':scenario,'target_rps':rate,'repetition':repetition,'state':state,'phase':'warmup','reason':str(error),'source':str(warmup.relative_to(ROOT))}
                save(failure,row);print(f'{framework} {scenario} {rate}: {state} during warm-up; evidence retained',flush=True)
                return {**row,'kind':'warmup_failure','sustainable':False},True
            if scenario in ['db-create','db-update','mixed']:self.database(reset=True)
            return self.load(framework,scenario,rate,self.config['BENCH_DURATION'],run_dir,repetition=repetition),True
        finally:self.compose('stop',framework,check=False);self.database(reset=True)

def sample_stats(ids, path, stop, origin=None):
    origin=time.monotonic() if origin is None else origin
    fields=['timestamp','elapsed_seconds','role','container','cpu_percent','memory_mb','memory_limit_mb','memory_percent','network_in_bytes','network_out_bytes','block_in_bytes','block_out_bytes','pids']
    def size(s):
        import re
        m=re.fullmatch(r'([\d.]+)\s*([A-Za-z]*)',s.strip())
        if not m: raise ValueError(s)
        return float(m[1])*{'B':1,'kB':1000,'KB':1000,'KiB':1024,'MB':10**6,'MiB':2**20,'GB':10**9,'GiB':2**30,'TB':10**12,'TiB':2**40,'':1}[m[2]]
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        while not stop.is_set():
            begin=time.monotonic()
            result=docker('stats','--no-stream','--format','{{json .}}',*ids.values(),check=False)
            for line in result.stdout.splitlines():
                try:
                    data=json.loads(line)
                    role=next(role for role,cid in ids.items() if cid.startswith(data['ID']) or cid==data['Name'])
                    memory,limit=map(size,data['MemUsage'].split('/'))
                    net=list(map(size,data['NetIO'].split('/')));block=list(map(size,data['BlockIO'].split('/')))
                    writer.writerow(dict(timestamp=utc(),elapsed_seconds=time.monotonic()-origin,role=role,container=data['ID'],cpu_percent=float(data['CPUPerc'].rstrip('%')),memory_mb=memory/2**20,memory_limit_mb=limit/2**20,memory_percent=float(data['MemPerc'].rstrip('%')),network_in_bytes=net[0],network_out_bytes=net[1],block_in_bytes=block[0],block_out_bytes=block[1],pids=int(data['PIDs'])))
                except (ValueError,KeyError,StopIteration): continue
            f.flush()
            stop.wait(max(0,1-(time.monotonic()-begin)))

@contextlib.contextmanager
def exclusive():
    lock=ROOT/'.benchmark-lock'
    try:lock.mkdir()
    except FileExistsError:raise RuntimeError('Another benchmark is running. If interrupted, verify no benchmark is active before removing .benchmark-lock.')
    try:yield
    finally:lock.rmdir()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['doctor','build','smoke-test','benchmark','benchmark-all','reset-db','monitor'])
    parser.add_argument('arguments',nargs='*')
    args=parser.parse_args()
    framework=args.arguments[0] if args.action=='benchmark' and args.arguments else None
    profile=(args.arguments[1] if len(args.arguments)>1 else 'small') if args.action=='benchmark' else (args.arguments[0] if args.arguments else 'small')
    if profile not in PROFILES:parser.error('Profile must be small, standard, or large')
    if args.action=='benchmark' and framework not in FRAMEWORKS:parser.error('Specify a required framework')
    controller=Controller(profile)
    if args.action=='doctor':controller.doctor();return
    with exclusive():
        controller.doctor()
        if args.action=='build':raise ValueError('Build with scripts/build.sh or scripts/build.ps1 so Docker Desktop proxy and registry settings are preserved')
        if args.action=='reset-db':controller.database(reset=True);return
        if args.action=='smoke-test':
            session=controller.session();controller.smoke_all(session);print('Smoke evidence: '+str(session));return
        if args.action=='monitor':raise ValueError('Monitoring is started and stopped automatically with each measured run')
        session=controller.session()
        try:
            controller.benchmark([framework] if framework else FRAMEWORKS.copy(),session)
            save(session/'completed.json',{'timestamp':utc(),'completed':True})
        except Exception as e:
            save(session/'failure.json',{'timestamp':utc(),'error':str(e)})
            raise
        print('Raw results: '+str(session),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:print(str(e),file=sys.stderr,flush=True);sys.exit(1)
