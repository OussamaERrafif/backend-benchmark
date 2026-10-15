"""Sustained outcomes must appear in reports before collection can complete."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import campaign
import runner
from campaign import InvestigatedController, finalize_campaign
from resume_reports import ResumeReports


class CampaignReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = self.root / 'session'
        self.session.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_each_stress_outcome_refreshes_report_and_runtime_context(self):
        controller = object.__new__(InvestigatedController)
        row = {'state': 'OUT_OF_MEMORY', 'rejected': True}
        with patch.object(runner, 'ROOT', self.root), patch.object(
                runner, 'command', return_value=subprocess.CompletedProcess([], 0)) as command:
            with ResumeReports(self.session) as reports:
                controller.resume_reports = reports
                with patch.object(runner.Controller, 'load', return_value=row):
                    result = controller.load('fiber', 'json', 1600, '600s',
                                             self.session / 'fiber/json/stress', kind='stress')
                    self.assertIs(result, row)
                command.assert_called_once_with(reports.pipeline)
                self.assertFalse(reports.dirty)
                state = json.loads((self.session / 'runtime.json').read_text())
                self.assertEqual(state['kind'], 'stress')
                self.assertEqual(state['scenario'], 'json')
                self.assertEqual(state['new_attempts'], 1)

    def test_warmup_does_not_refresh_report_as_a_stress_outcome(self):
        controller = object.__new__(InvestigatedController)
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'command') as command:
            with ResumeReports(self.session) as reports:
                controller.resume_reports = reports
                with patch.object(runner.Controller, 'load', return_value=None):
                    controller.load('fiber', 'json', 1600, '60s', self.session / 'warmup',
                                    measured=False)
                command.assert_not_called()
                self.assertEqual(reports.collected, 0)

    def test_integrity_failure_cannot_publish_complete(self):
        state = {'state': 'RUNNING'}
        with patch.object(campaign, 'STATE', self.root / 'campaign.json'), patch.object(
                campaign, 'command'), patch('verify_report.verify', side_effect=RuntimeError('missing stress')):
            with self.assertRaisesRegex(RuntimeError, 'missing stress'):
                finalize_campaign(state)
        saved = json.loads((self.root / 'campaign.json').read_text())
        self.assertEqual(saved['state'], 'VALIDATING')
        self.assertNotIn('completed_at', saved)

    def test_complete_is_published_only_after_integrity_passes(self):
        state = {'state': 'RUNNING'}
        observed = []
        def verify(**kwargs):
            observed.append(json.loads((self.root / 'campaign.json').read_text())['state'])
            self.assertEqual(kwargs, {'expected_state': 'VALIDATING'})
        with patch.object(campaign, 'STATE', self.root / 'campaign.json'), patch.object(
                campaign, 'command') as command, patch('verify_report.verify', side_effect=verify):
            finalize_campaign(state)
        self.assertEqual(observed, ['VALIDATING'])
        self.assertEqual(state['state'], 'COMPLETE')
        self.assertEqual(command.call_count, 2)


if __name__ == '__main__':
    unittest.main()
