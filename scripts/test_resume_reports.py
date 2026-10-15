"""Verify cached reporting never changes collection or hides new outcomes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import runner
from resume_reports import ResumeReports


class ResumeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(runner, 'ROOT', self.root)
        self.root_patch.start()
        self.session = self.root / 'session'
        self.session.mkdir()
        for name in ('reports/benchmark-report.md', 'reports/benchmark-report.html',
                     'reports/charts/artifacts.json', 'results/processed/results.csv'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('existing report')

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_completed_levels_skip_report_builds_and_restore_command(self):
        with patch.object(runner, 'command') as command:
            with ResumeReports(self.session) as reports:
                for repetition in range(3):
                    reports.begin_attempt('fastapi', 'compute', 100, repetition, cached=True)
                    reports.outcome(False)
                    self.assertEqual(runner.command(reports.pipeline).returncode, 0)
                command.assert_not_called()
            self.assertIs(runner.command, command)
        state = json.loads((self.session / 'runtime.json').read_text())
        self.assertEqual(state['reused_attempts'], 3)
        self.assertEqual(state['skipped_unchanged_reports'], 3)

    def test_fresh_outcomes_generate_once_per_changed_level(self):
        with patch.object(runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                reports.begin_attempt('fastapi', 'compute', 100, 1, cached=False)
                reports.outcome(True)
                runner.command(reports.pipeline)
                runner.command(reports.pipeline)
                command.assert_called_once()

    def test_incomplete_fresh_attempt_still_regenerates_on_cleanup(self):
        with patch.object(runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                reports.begin_attempt('fastapi', 'compute', 100, 1, cached=False)
                runner.command(reports.pipeline)
                command.assert_called_once()

    def test_missing_report_and_other_commands_are_never_skipped(self):
        (self.root / 'reports/benchmark-report.html').unlink()
        with patch.object(runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                runner.command(reports.pipeline)
                runner.command(['docker', 'inspect', 'test'])
                self.assertEqual(command.call_count, 2)

    def test_report_errors_propagate_and_command_is_restored(self):
        with patch.object(runner, 'command', side_effect=RuntimeError('report failed')) as command:
            with self.assertRaisesRegex(RuntimeError, 'report failed'):
                with ResumeReports(self.session) as reports:
                    reports.begin_attempt('fastapi', 'compute', 100, 1, cached=False)
                    runner.command(reports.pipeline)
            self.assertIs(runner.command, command)
        self.assertEqual(json.loads((self.session / 'runtime.json').read_text())['phase'], 'ERROR')

    def test_interrupted_report_is_refreshed_once_before_replay(self):
        marker = self.session / 'report-refresh-required.json'
        marker.write_text('{}')
        with patch.object(runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                runner.command(reports.pipeline)
                runner.command(reports.pipeline)
                command.assert_called_once()
                self.assertFalse(marker.exists())

    def test_saved_stress_newer_than_processed_output_refreshes_on_resume(self):
        output = self.root / 'results/processed/stress-results.csv'
        output.write_text('old processed stress')
        metadata = self.session / 'fiber/json/stress/metadata.json'
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({'finished_at': '2026-10-12T00:00:00Z'}))
        os.utime(output, ns=(1_000_000_000, 1_000_000_000))
        before = metadata.read_bytes()
        with patch.object(runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                runner.command(reports.pipeline)
                runner.command(reports.pipeline)
                command.assert_called_once()
        self.assertEqual(metadata.read_bytes(), before)

    def test_stress_refresh_marker_survives_report_failure(self):
        with patch.object(runner, 'command', side_effect=RuntimeError('report failed')):
            with self.assertRaisesRegex(RuntimeError, 'report failed'):
                with ResumeReports(self.session) as reports:
                    reports.begin_stress('fiber', 'json', 1600)
                    reports.outcome(True)
                    runner.command(reports.pipeline)
        self.assertTrue((self.session / 'report-refresh-required.json').exists())
        self.assertTrue(ResumeReports(self.session).dirty)


if __name__ == '__main__':
    unittest.main()
