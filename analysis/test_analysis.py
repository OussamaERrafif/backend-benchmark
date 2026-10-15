"""Validator regression tests use temporary data derived from real runs only."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
import gzip
import sys
from process_results import ROOT, aggregate, parse_run, sustainable, stress_windows
from experiment_audit import coverage
from create_charts import point_state, panel_groups, missing_label, chart, status_chart, with_warmup_failures
sys.path.insert(0,str(ROOT/'scripts'))
from runner import Controller
import pandas as pd

class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates=list((ROOT/'results/raw').glob('*/*/*/*/run-*'))
        cls.original=next((p for p in candidates if (p/'validation.json').exists() and not json.loads((p/'validation.json').read_text()).get('rejected')),None)
        if cls.original is None:raise unittest.SkipTest('A real measured run is required for validator regression tests')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        session=Path(self.temp.name)/'measured-session'
        original_session=self.original.parents[3]
        session.mkdir()
        for file in ['session.json','versions.json']:
            shutil.copyfile(original_session/file,session/file)
        self.run=session/'framework/scenario/100-rps/run-1'
        shutil.copytree(self.original,self.run)

    def tearDown(self):self.temp.cleanup()

    def mutate_summary(self, fn):
        path=self.run/'k6-summary.json';data=json.loads(path.read_text());fn(data);path.write_text(json.dumps(data))

    def scenario_window(self, drain=0):
        path=self.run/'metadata.json';meta=json.loads(path.read_text())
        meta['throughput_window']='scenario-monotonic-v2'
        stats=pd.read_csv(self.run/'docker-stats.csv')
        start=pd.to_datetime(stats[stats.role=='backend'].timestamp,utc=True).astype('int64').min()/1e6
        meta['monitor_started_at']=pd.Timestamp(start/1000,unit='s',tz='UTC').isoformat()
        path.write_text(json.dumps(meta))
        stats['elapsed_seconds']=pd.to_datetime(stats.timestamp,utc=True).astype('int64')/1e9-start/1000
        stats.to_csv(self.run/'docker-stats.csv',index=False)
        elapsed=meta['duration_seconds']+drain
        self.mutate_summary(lambda d:d.update(benchmark_window={'method':'scenario-monotonic-v2','started_at_ms':start,'finished_at_ms':start+elapsed*1000,'duration_seconds':elapsed,'scheduled_duration_seconds':meta['duration_seconds']}))
        return meta, start

    def test_scenario_throughput_excludes_k6_initialization(self):
        meta,_=self.scenario_window()
        self.mutate_summary(lambda d:d['metrics']['http_reqs']['values'].update(rate=1))
        result=parse_run(self.run)
        self.assertFalse(result['rejected'])
        self.assertAlmostEqual(result['achieved_rps'],result['requests']/meta['duration_seconds'])

    def test_scenario_throughput_includes_actual_request_drain(self):
        meta,_=self.scenario_window(drain=5)
        result=parse_run(self.run)
        self.assertFalse(result['rejected'])
        self.assertAlmostEqual(result['achieved_rps'],result['requests']/(meta['duration_seconds']+5))

    def test_scenario_measurement_requires_timing_evidence(self):
        self.scenario_window()
        self.mutate_summary(lambda d:d.pop('benchmark_window'))
        self.assertTrue(parse_run(self.run)['rejected'])

    def test_scenario_measurement_rejects_inconsistent_timing(self):
        self.scenario_window()
        self.mutate_summary(lambda d:d['benchmark_window'].update(duration_seconds=1))
        self.assertTrue(parse_run(self.run)['rejected'])

    def test_scenario_resource_statistics_exclude_initialization(self):
        _,start=self.scenario_window();before=parse_run(self.run)
        path=self.run/'docker-stats.csv';stats=pd.read_csv(path)
        extra=stats[stats.role=='backend'].iloc[0].copy()
        extra['timestamp']=pd.Timestamp(start/1000-1,unit='s',tz='UTC').isoformat()
        extra['elapsed_seconds']=-1
        extra['cpu_percent']=10000;extra['memory_mb']=10000
        pd.concat([pd.DataFrame([extra]),stats]).to_csv(path,index=False)
        after=parse_run(self.run)
        self.assertEqual(before['cpu_avg_percent'],after['cpu_avg_percent'])
        self.assertEqual(before['ram_peak_mb'],after['ram_peak_mb'])

    def test_wall_clock_adjustment_does_not_change_monotonic_metrics(self):
        self.scenario_window();before=parse_run(self.run)
        path=self.run/'docker-stats.csv';stats=pd.read_csv(path)
        stats['timestamp']=(pd.to_datetime(stats.timestamp,utc=True)+pd.Timedelta(hours=1)).astype(str)
        stats.to_csv(path,index=False)
        after=parse_run(self.run)
        self.assertFalse(after['rejected'])
        for field in ['achieved_rps','cpu_avg_percent','cpu_seconds','ram_avg_mb']:
            self.assertEqual(before[field],after[field])

    def test_real_run_accepted(self):
        self.assertFalse(parse_run(self.run)['rejected'])

    def test_failure_annotations_keep_multi_panel_charts_readable(self):
        from matplotlib import pyplot as plt
        row = parse_run(self.run)
        rows = []
        for scenario in ['plaintext','json','json-large','compute','db-read','db-list','db-create','db-update','mixed']:
            for framework in ['springboot','fastapi','laravel','express','fastify','aspnet','fiber','axum']:
                point = {**row, 'framework': framework, 'scenario': scenario,
                         'states': 'PASS', 'all_sustainable': True}
                rows.extend([point, {**point, 'target_rps': 1000, 'states': 'OUT_OF_MEMORY',
                                     'latency_p95_ms': float('nan')}])
        figure = None
        try:
            with patch('create_charts.plt.close') as close:
                chart(Path(self.temp.name)/'annotation-layout.png', pd.DataFrame(rows),
                      'latency_p95_ms', 'p95 latency (ms)', curve=True, x='target_rps')
                figure = close.call_args.args[0]
                self.assertTrue(all(ax.get_position().width >= .20 for ax in figure.axes))
                self.assertTrue(all(ax.get_position().height >= .12 for ax in figure.axes))
        finally:
            if figure is not None: plt.close(figure)

    def test_missing_resource_measurement_rejected(self):
        (self.run/'docker-stats.csv').unlink()
        result=parse_run(self.run)
        self.assertTrue(result['rejected']);self.assertEqual(result['state'],'BENCHMARK_ERROR')

    def test_inconsistent_error_count_rejected(self):
        self.mutate_summary(lambda d:d['metrics']['http_req_failed']['values'].update(passes=d['metrics']['http_reqs']['values']['count']))
        self.assertTrue(parse_run(self.run)['rejected'])

    def test_nonfinite_latency_rejected(self):
        self.mutate_summary(lambda d:d['metrics']['http_req_duration']['values'].update({'p(99)':float('nan')}))
        self.assertTrue(parse_run(self.run)['rejected'])

    def test_oom_classification(self):
        path=self.run/'metadata.json';meta=json.loads(path.read_text());meta['backend_state']['OOMKilled']=True;path.write_text(json.dumps(meta))
        result=parse_run(self.run)
        self.assertTrue(result['rejected']);self.assertEqual(result['state'],'OUT_OF_MEMORY')

    def test_load_generator_oom_is_not_a_backend_oom(self):
        path=self.run/'metadata.json';meta=json.loads(path.read_text());meta['k6_state']={'Running':False,'OOMKilled':True,'ExitCode':137};path.write_text(json.dumps(meta))
        result=parse_run(self.run)
        self.assertTrue(result['rejected']);self.assertEqual(result['state'],'LOAD_GENERATOR_OOM')
        self.assertIn('k6',result['reason'])

    def test_running_backend_health_timeout_is_not_a_crash(self):
        path=self.run/'metadata.json';meta=json.loads(path.read_text());meta['backend_healthy']=False;path.write_text(json.dumps(meta))
        result=parse_run(self.run)
        self.assertFalse(result['rejected']);self.assertEqual(result['state'],'HEALTH_CHECK_FAILED')

    def test_missing_repetitions_cannot_be_sustainable(self):
        row=parse_run(self.run);row['required_repetitions']=3;row['sustainable']=True
        result=aggregate(pd.DataFrame([row]))
        self.assertFalse(result.iloc[0]['all_sustainable'])

    def test_rejected_run_does_not_contribute_to_aggregate(self):
        row=parse_run(self.run);row['rejected']=True
        result=aggregate(pd.DataFrame([row]))
        self.assertFalse(result.iloc[0]['all_sustainable'])
        self.assertNotIn('achieved_rps',result.columns)

    def test_failed_measurement_remains_in_median_and_marker(self):
        row=parse_run(self.run);row.update(sustainable=False,state='ERROR_LIMIT')
        result=aggregate(pd.DataFrame([row])).iloc[0]
        self.assertEqual(result.achieved_rps,row['achieved_rps'])
        self.assertEqual(point_state(result),'ERROR_LIMIT')

    def test_incomplete_pass_has_distinct_marker(self):
        row=parse_run(self.run);row.update(required_repetitions=3,sustainable=True,state='PASS')
        self.assertEqual(point_state(aggregate(pd.DataFrame([row])).iloc[0]),'INCOMPLETE')

    def test_coverage_distinguishes_unrun_and_failure(self):
        row=parse_run(self.run);row.update(framework='fiber',scenario='compute',sustainable=False,state='LATENCY_LIMIT')
        result=coverage(pd.DataFrame([row])).set_index(['scenario','framework'])
        self.assertEqual(result.loc[('compute','fiber'),'status'],'NO_PASSING_LEVEL')
        self.assertEqual(result.loc[('compute','axum'),'status'],'NOT_RUN')
        self.assertEqual(int(result.normal_runs.sum()),1)

    def test_sessions_and_images_are_separate_panels_and_aggregates(self):
        row=parse_run(self.run);other={**row,'session_id':'another-session'}
        groups=panel_groups(aggregate(pd.DataFrame([row,other])))
        self.assertEqual(len(groups),2)
        other={**row,'image_id':'another-image'}
        self.assertEqual(len(aggregate(pd.DataFrame([row,other]))),2)

    def test_sustainable_maxima_keep_builds_separate(self):
        row=parse_run(self.run);row.update(required_repetitions=1,sustainable=True,state='PASS')
        other={**row,'image_id':'another-image'}
        self.assertEqual(len(sustainable(aggregate(pd.DataFrame([row,other])))),2)

    def test_crash_without_numbers_is_visible_in_status_chart(self):
        row=parse_run(self.run);row.update(rejected=True,sustainable=False,state='BACKEND_CRASH')
        summary=aggregate(pd.DataFrame([row]))
        self.assertEqual(point_state(summary.iloc[0]),'BACKEND_CRASH')
        output=Path(self.temp.name)/'status.png';status_chart(output,summary,True)
        self.assertGreater(output.stat().st_size,1000)

    def test_bar_missing_label_distinguishes_never_run(self):
        frame=pd.DataFrame([parse_run(self.run)])
        self.assertEqual(missing_label(frame.iloc[0].framework,frame),'no sustainable level')
        self.assertEqual(missing_label('never-run',frame),'not run')

    def test_failed_only_numeric_curve_renders(self):
        row=parse_run(self.run);row.update(sustainable=False,state='ERROR_LIMIT')
        output=Path(self.temp.name)/'curve.png'
        chart(output,aggregate(pd.DataFrame([row])),'latency_p99_ms','p99',curve=True)
        self.assertGreater(output.stat().st_size,1000)

    def test_empty_chart_is_removed_instead_of_publishing_a_placeholder(self):
        output=Path(self.temp.name)/'empty.png';output.write_bytes(b'old generated placeholder')
        self.assertFalse(chart(output,pd.DataFrame(),'latency_p99_ms','p99',curve=True))
        self.assertFalse(output.exists())

    def test_planned_research_coverage_is_pending_without_fake_measurements(self):
        # Only diagnostic data exists, so every planned research cell must stay pending.
        row={**parse_run(self.run),'mode':'quick'}
        result=coverage(pd.DataFrame([row]),planned=[('research','standard')])
        planned=result[(result['mode']=='research')&(result.profile=='standard')]
        self.assertEqual(len(planned),72)
        self.assertTrue((planned.status=='PENDING').all())
        self.assertEqual(int(planned.normal_runs.sum()),0)

    def test_exited_zero_stats_do_not_reduce_generator_averages(self):
        before=parse_run(self.run)
        path=self.run/'docker-stats.csv';stats=pd.read_csv(path)
        stopped=stats[stats.role=='k6'].iloc[-1].copy();stopped['cpu_percent']=0;stopped['memory_mb']=0
        pd.concat([stats,pd.DataFrame([stopped])]).to_csv(path,index=False)
        after=parse_run(self.run)
        self.assertEqual(before['k6_cpu_avg_percent'],after['k6_cpu_avg_percent'])
        self.assertEqual(before['k6_ram_avg_mb'],after['k6_ram_avg_mb'])

    def test_interrupted_attempt_retains_failure_without_numeric_repetition(self):
        (self.run/'interrupted.json').write_text(json.dumps({'reason':'Interrupted in regression check'}))
        result=parse_run(self.run)
        self.assertEqual(result['kind'],'interrupted')
        self.assertTrue(result['rejected'])
        self.assertEqual(result['state'],'BENCHMARK_ERROR')
        self.assertNotIn('achieved_rps',result)

    def test_missing_backend_evidence_is_not_called_a_crash(self):
        path=self.run/'metadata.json';meta=json.loads(path.read_text());meta.pop('backend_state');path.write_text(json.dumps(meta))
        result=parse_run(self.run)
        self.assertEqual(result['state'],'BENCHMARK_ERROR')

    def test_gzip_time_series_preserve_measured_windows(self):
        original=next((ROOT/'results/raw').glob('*/*/*/stress/k6-timeseries.json'),None)
        if original is None:self.skipTest('A real stress diagnostic is required')
        directory=Path(self.temp.name)/'compressed-stress';directory.mkdir()
        shutil.copyfile(original.parent/'metadata.json',directory/'metadata.json')
        shutil.copyfile(original.parent/'k6-summary.json',directory/'k6-summary.json')
        with original.open('rb') as source,gzip.open(directory/'k6-timeseries.json.gz','wb') as target:shutil.copyfileobj(source,target)
        self.assertEqual(stress_windows(original.parent),stress_windows(directory))

    def test_monotonic_stress_windows_preserve_all_real_http_requests(self):
        original=next((ROOT/'results/raw').glob('timing-integration-*/express/json/window-check/k6-timeseries.json.gz'),None)
        if original is None:self.skipTest('A real monotonic timing check is required')
        summary=json.loads((original.parent/'k6-summary.json').read_text())
        windows=stress_windows(original.parent)
        requests=sum(window['rps']*window['window_seconds'] for window in windows)
        self.assertAlmostEqual(requests,summary['metrics']['http_reqs']['values']['count'])

    def test_failed_warmups_do_not_inflate_measured_repetition_count(self):
        row=parse_run(self.run);row['required_repetitions']=3
        summary=aggregate(pd.DataFrame([row]))
        warmups=pd.DataFrame([{**{key:row[key] for key in ['session_id','framework','profile','scenario','target_rps']},'repetition':rep,'state':'HEALTH_CHECK_FAILED'} for rep in [2,3]])
        result=with_warmup_failures(summary,warmups).iloc[0]
        self.assertEqual(int(result.repetitions),1)
        self.assertEqual(int(result.warmup_failures),2)
        self.assertEqual(point_state(result),'HEALTH_CHECK_FAILED')

    def test_resume_reuses_completed_evidence_without_new_backend_load(self):
        controller=Controller.__new__(Controller);controller.start=Mock();controller.database=Mock()
        before=(self.run/'metadata.json').read_bytes()
        row,fresh=controller.collect_repetition('framework','scenario',100,1,self.run.parents[3])
        self.assertFalse(fresh);self.assertFalse(row['rejected'])
        controller.start.assert_not_called();controller.database.assert_not_called()
        self.assertEqual((self.run/'metadata.json').read_bytes(),before)

    def test_adaptive_stop_waits_for_all_repetitions_at_each_load(self):
        controller=Controller.__new__(Controller)
        controller.cpu=2;controller.ram_mb=2048;controller.profile='standard'
        controller.config={'REPETITIONS':'3','RPS_STEPS':'100,500,1000','SCENARIOS':'json','RANDOM_SEED':'1','COOLDOWN_SECONDS':'0','RUN_STRESS':'0'}
        for name in ['smoke_all','database','start','startup','compose','stop_backends']:setattr(controller,name,Mock())
        template=parse_run(self.run);calls=[]
        def collect(framework,scenario,rate,repetition,session):
            calls.append((framework,rate,repetition))
            return {**template,'framework':framework,'target_rps':rate,'repetition':repetition,'sustainable':False,'state':'ERROR_LIMIT','rejected':False},True
        controller.collect_repetition=collect
        session=Path(self.temp.name)/'ordering';session.mkdir()
        with patch('runner.command'):controller.benchmark(['fiber','express'],session)
        self.assertEqual(len(calls),12)
        self.assertEqual([rate for _,rate,_ in calls],[100]*6+[500]*6)
        for framework in ['fiber','express']:
            for target in [100,500]:self.assertEqual({rep for name,rate,rep in calls if name==framework and rate==target},{1,2,3})

if __name__=='__main__':unittest.main()
