"""Durable complete research campaign, with immutable measurements and checkpoints."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import runner
from runner import Controller, FRAMEWORKS, SCENARIOS, ROOT, command, docker, save, utc
from recover_suspended import recover_session
from caddy_format import recorded_caddy_format
from resume_reports import ResumeReports
sys.path.insert(0, str(ROOT / 'analysis'))
from investigated_attempts import reviewed_rejection

STATE=ROOT/'verification/research-campaign.json'


class InvestigatedController(Controller):
    def load(self, framework, scenario, rate, seconds, directory,
             measured=True, repetition=1, kind='normal'):
        reports = getattr(self, 'resume_reports', None)
        sustained = measured and kind == 'stress' and reports is not None
        if sustained:
            reports.begin_stress(framework, scenario, rate)
        row = super().load(framework, scenario, rate, seconds, directory,
                           measured=measured, repetition=repetition, kind=kind)
        if sustained:
            reports.outcome(True)
            runner.command(reports.pipeline)
        return row

    def benchmark(self, frameworks, session):
        with ResumeReports(session) as reports:
            self.resume_reports = reports
            try:
                return super().benchmark(frameworks, session)
            finally:
                self.resume_reports = None

    def collect_repetition(self, framework, scenario, rate, repetition, session):
        reports = getattr(self, 'resume_reports', None)
        if reports is not None:
            directory = session / framework / scenario / f'{rate}-rps' / f'run-{repetition}'
            metadata = directory / 'metadata.json'
            cached = (directory.parent / f'warmup-failed-{repetition}.json').exists()
            if metadata.exists():
                cached = cached or bool(json.loads(metadata.read_text()).get('finished_at'))
            reports.begin_attempt(framework, scenario, rate, repetition, cached)
        row, fresh = super().collect_repetition(framework, scenario, rate, repetition, session)
        if reports is not None:
            reports.outcome(fresh)
        directory = session / framework / scenario / f'{rate}-rps' / f'run-{repetition}'
        record = reviewed_rejection(directory, row)
        if record is not None:
            # This is an adaptive-stop outcome, never an accepted measurement.
            # Keep the raw validator result and rejected flag intact. Only an
            # individually reviewed, hash-verified attempt can bypass the halt.
            row = {**row, 'state': 'MEASUREMENT_REJECTED', 'sustainable': False}
            print(f'{framework} {scenario} {rate} RPS run {repetition}: '
                  'MEASUREMENT_REJECTED (investigated; metrics excluded; no retry)', flush=True)
        return row, fresh

def atomic_save(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    save(temporary,value);os.replace(temporary,path)


def finalize_campaign(state):
    # Do not publish COMPLETE until the full coverage/integrity gate succeeds.
    state['state'] = 'VALIDATING'
    state['updated_at'] = utc()
    atomic_save(STATE, state)
    command(['python', str(ROOT / 'analysis/pipeline.py')])
    from verify_report import verify
    verify(expected_state='VALIDATING')
    state['state'] = 'COMPLETE'
    state['completed_at'] = utc()
    atomic_save(STATE, state)
    command(['python', str(ROOT / 'analysis/pipeline.py')])

def acquire_lock():
    lock=ROOT/'.benchmark-lock'
    owner=lock/'owner.json'
    hostname=os.environ['HOSTNAME']
    if lock.exists():
        if not owner.exists():raise RuntimeError('Another controller owns .benchmark-lock; refusing to take over')
        previous=json.loads(owner.read_text())
        result=docker('inspect',previous['container'],check=False)
        running=result.returncode==0 and json.loads(result.stdout)[0]['State']['Running']
        if running and previous['container']!=hostname:raise RuntimeError('Another live controller owns the workspace')
    else:lock.mkdir()
    atomic_save(owner,{'container':hostname,'campaign':'full-research','started_at':utc()})
    return lock,owner

def verify_snapshot(controller,session):
    expected=json.loads((session/'session.json').read_text())['config']
    if {k:v for k,v in controller.config.items() if k!='POSTGRES_PASSWORD'}!=expected:raise RuntimeError('Benchmark configuration changed since campaign start')
    snapshot=json.loads((session/'versions.json').read_text())
    for framework in FRAMEWORKS+['postgres','k6']:
        current=json.loads(docker('image','inspect',controller.spec['services'][framework]['image']).stdout)[0]['Id']
        if current!=snapshot['resolved_images'][framework]['id']:raise RuntimeError('Build changed since campaign start: '+framework)
    manifest=json.loads((session/'source-manifest.json').read_text())
    for relative,digest in manifest.items():
        if relative.startswith(('apps/','fixtures/','load-tests/','database/')) or relative in {'compose.yml','versions.lock','scripts/runner.py','analysis/process_results.py'}:
            current_digest=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
            if current_digest!=digest:
                # Only this recorded formatter output is permitted, after the
                # pinned binary produced identical parsed JSON for 1/2/4 workers.
                if relative=='apps/laravel/Caddyfile' and recorded_caddy_format(session,digest,current_digest):continue
                raise RuntimeError('Measured application/harness source changed: '+relative)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profiles',nargs='+',choices=['small','standard','large'],default=['standard'])
    args=parser.parse_args()
    # Explicit complete methodology, unaffected by a leftover quick-mode .env.
    settings=dict(BENCH_MODE='research',REPETITIONS='3',WARMUP_DURATION='60s',BENCH_DURATION='120s',RPS_STEPS='50,100,250,500,1000,2000,4000,8000',SCENARIOS='json,'+','.join(s for s in SCENARIOS if s!='json'),COOLDOWN_SECONDS='30',STRESS_DURATION='600s',RUN_STRESS='1')
    os.environ.update(settings)
    state=json.loads(STATE.read_text()) if STATE.exists() else {'created_at':utc(),'profiles':args.profiles,'config':settings,'sessions':{},'state':'INITIALIZING'}
    if state['profiles']!=args.profiles or state['config']!=settings:raise RuntimeError('Existing campaign has a different scope; preserve it and choose a new campaign state path')
    if state.get('state')=='COMPLETE':
        docker('update','--restart=no',os.environ['HOSTNAME'],check=False);return
    lock,owner=acquire_lock()
    try:
        # A prior controller may have died while its sibling k6 container continued.
        # Retain logs, then stop only this Compose project's named load generators.
        for cid in docker('ps','-a','-q','--filter','label=com.docker.compose.project=backend-benchmark','--filter','label=com.docker.compose.service=k6').stdout.split():
            item=json.loads(docker('inspect',cid).stdout)[0]
            if item['Name'].lstrip('/').startswith('benchmark-k6-'):
                logs=docker('logs',cid,check=False)
                (ROOT/'verification'/('interrupted-'+item['Name'].lstrip('/')+'.log')).write_text(logs.stdout+logs.stderr)
                docker('rm','-f',cid)
        state['state']='RUNNING';state['started_at']=utc();state['container']=os.environ['HOSTNAME'];state['error']=None
        state['planned_maximum_measurements']=len(args.profiles)*len(FRAMEWORKS)*len(SCENARIOS)*8*3
        state['note']='Higher loads stop after two consecutive failed levels; crashes/OOM and failed warm-ups are preserved. Completion requires every selected framework/workload and every repetition to be visited.'
        state['scope']='All eight frameworks and all nine workloads; every visited load receives three repetitions before adaptive stopping; 60s warm-up, 120s measurement, 600s stress.'
        atomic_save(STATE,state)
        for profile in args.profiles:
            controller=InvestigatedController(profile);controller.doctor()
            session=ROOT/'results/raw'/state['sessions'][profile] if profile in state['sessions'] else controller.session()
            if profile not in state['sessions']:
                state['sessions'][profile]=session.name;atomic_save(STATE,state)
            verify_snapshot(controller,session)
            if (session/'completed.json').exists():continue
            state['current_profile']=profile;atomic_save(STATE,state)
            recover_session(session)
            # Only validator-rejected clock-discontinuity attempts can be retried.
            # Bound recovery so an unstable host cannot restart the sweep forever.
            for recovery_attempt in range(3):
                try:
                    controller.benchmark(FRAMEWORKS.copy(),session)
                    break
                except RuntimeError:
                    if recovery_attempt == 2 or not recover_session(session):raise
            # Verify required repetition coverage, allowing explicitly recorded warm-up
            # failures at the safety boundary rather than inventing numeric metrics.
            missing=[]
            for framework in FRAMEWORKS:
                for scenario in SCENARIOS:
                    for repetition in range(1,4):
                        runs=list((session/framework/scenario).glob(f'*-rps/run-{repetition}/metadata.json'))
                        failures=list((session/framework/scenario).glob(f'*-rps/warmup-failed-{repetition}.json'))
                        if not runs and not failures:missing.append(f'{framework}/{scenario}/repetition-{repetition}')
            if missing:raise RuntimeError('Campaign coverage is incomplete: '+', '.join(missing))
            save(session/'completed.json',{'timestamp':utc(),'completed':True,'purpose':'Full research matrix for all eight frameworks and all nine scenarios; three repetitions and ten-minute stress tests'})
            save(session/'progress.json',{'timestamp':utc(),'state':'COMPLETE'})
        finalize_campaign(state)
        controller.stop_backends();controller.compose('stop','postgres',check=False)
        print('Full research report completed: /workspace/reports/benchmark-report.md',flush=True)
    except Exception as error:
        state['state']='NEEDS_ATTENTION';state['error']=str(error);state['updated_at']=utc();atomic_save(STATE,state)
        if 'controller' in locals():controller.stop_backends()
        command(['python',str(ROOT/'analysis/pipeline.py')],check=False)
        raise
    finally:
        # Finished and failed campaigns do not restart forever. A Docker/Desktop
        # restart during an active campaign resumes through the checkpoints above.
        docker('update','--restart=no',os.environ['HOSTNAME'],check=False)
        owner.unlink(missing_ok=True);lock.rmdir()

if __name__=='__main__':
    try:main()
    except Exception as error:print(str(error),file=sys.stderr,flush=True);sys.exit(1)
