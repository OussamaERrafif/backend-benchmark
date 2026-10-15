"""Avoid rebuilding identical reports while restoring a completed load ladder."""
import json
import os
from pathlib import Path
import subprocess
import uuid
import runner


class ResumeReports:
    def __init__(self, session):
        self.session = Path(session)
        self.pipeline = ['python', str(runner.ROOT / 'analysis/pipeline.py')]
        self.refresh_marker = self.session / 'report-refresh-required.json'
        self.dirty = self.refresh_marker.exists() or not all((runner.ROOT / name).exists() for name in (
            'reports/benchmark-report.md', 'reports/benchmark-report.html',
            'reports/charts/artifacts.json', 'results/processed/results.csv'))
        stress_output = runner.ROOT / 'results/processed/stress-results.csv'
        stress_updated = stress_output.stat().st_mtime_ns if stress_output.exists() else 0
        # Older workers did not invalidate reports after sustained tests. Detect
        # those saved outcomes on resume without changing their raw evidence.
        self.dirty = self.dirty or any(
            path.stat().st_mtime_ns > stress_updated
            and json.loads(path.read_text()).get('finished_at')
            for path in self.session.glob('*/*/stress/metadata.json'))
        self.reused = 0
        self.collected = 0
        self.skipped_reports = 0
        self.context = {}

    def update(self, phase):
        path = self.session / 'runtime.json'
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        runner.save(temporary, {'timestamp': runner.utc(), 'phase': phase,
                    'reused_attempts': self.reused, 'new_attempts': self.collected,
                    'skipped_unchanged_reports': self.skipped_reports, **self.context})
        os.replace(temporary, path)

    def begin_attempt(self, framework, scenario, rate, repetition, cached):
        self.context = dict(framework=framework, scenario=scenario,
                            target_rps=rate, repetition=repetition)
        if not cached:
            # Mark before collection so failed/incomplete fresh attempts also
            # trigger the original final report generation on an exception.
            self.dirty = True
            runner.save(self.refresh_marker, {'timestamp': runner.utc(), **self.context})
        self.update('RESUMING' if cached else 'COLLECTING')

    def begin_stress(self, framework, scenario, rate):
        self.begin_attempt(framework, scenario, rate, 1, cached=False)
        self.context['kind'] = 'stress'
        self.update('STRESS')

    def outcome(self, fresh):
        if fresh:
            self.collected += 1
            self.dirty = True
        else:
            self.reused += 1
        self.update('COLLECTED' if fresh else 'RESUMING')

    def __enter__(self):
        self.original_command = runner.command

        def report_aware_command(args, *positional, **kwargs):
            if list(args) != self.pipeline:
                return self.original_command(args, *positional, **kwargs)
            if not self.dirty:
                self.skipped_reports += 1
                self.update('RESUMING')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
            self.update('REPORTING')
            result = self.original_command(args, *positional, **kwargs)
            if result.returncode == 0:
                self.dirty = False
                self.refresh_marker.unlink(missing_ok=True)
            return result

        self.update('RESUMING')
        runner.command = report_aware_command
        return self

    def __exit__(self, exc_type, exc, traceback):
        runner.command = self.original_command
        self.update('ERROR' if exc_type else 'FINALIZING')
