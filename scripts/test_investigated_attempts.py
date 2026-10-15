"""A reviewed timing failure stays rejected and cannot authorize unrelated data."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from runner import ROOT, Controller
sys.path.insert(0, str(ROOT / 'analysis'))
from investigated_attempts import investigation_path, reviewed_rejection
from process_results import parse_run, aggregate
from campaign import InvestigatedController
import pandas as pd


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        campaign = ROOT / 'verification/research-campaign.json'
        sid = json.loads(campaign.read_text())['sessions']['standard'] if campaign.exists() else None
        source = ROOT / 'results/raw' / str(sid)
        if sid is None or not (source / 'fastapi/compute/500-rps/run-3').exists():
            self.skipTest('Requires the preserved real timing-rejection fixture from a research campaign')
        self.temp = tempfile.TemporaryDirectory()
        self.session = Path(self.temp.name) / 'session'
        self.session.mkdir()
        for name in ('session.json', 'versions.json'):
            shutil.copyfile(source / name, self.session / name)
        self.run = self.session / 'fastapi/compute/500-rps/run-3'
        shutil.copytree(source / 'fastapi/compute/500-rps/run-3', self.run)
        self.row = parse_run(self.run)
        self.assertEqual(self.row['reason'], 'Invalid scenario measurement window')
        path, relative = investigation_path(self.run)
        self.record_path = path
        path.parent.mkdir()
        self.record = dict(relative_source=relative, resolution='retain_rejected_as_failed_load',
                           validation_reason=self.row['reason'], raw_sha256={
                               p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in self.run.iterdir() if p.is_file()})

    def tearDown(self):
        self.temp.cleanup()

    def write_record(self):
        self.record_path.write_text(json.dumps(self.record))

    def outcome(self):
        controller = object.__new__(InvestigatedController)
        with patch.object(Controller, 'collect_repetition', return_value=(self.row, False)):
            return controller.collect_repetition('fastapi', 'compute', 500, 3, self.session)

    def test_unreviewed_rejection_still_halts(self):
        self.assertIsNone(reviewed_rejection(self.run, self.row))
        self.assertEqual(self.outcome()[0]['state'], 'BENCHMARK_ERROR')

    def test_reviewed_outcome_is_rejected_unsustainable_and_not_retried(self):
        self.write_record()
        outcome, fresh = self.outcome()
        self.assertEqual(outcome['state'], 'MEASUREMENT_REJECTED')
        self.assertTrue(outcome['rejected'])
        self.assertFalse(outcome['sustainable'])
        self.assertFalse(fresh)
        self.assertEqual(parse_run(self.run)['state'], 'BENCHMARK_ERROR')
        summary = aggregate(pd.DataFrame([self.row]))
        self.assertEqual(summary.iloc[0]['valid_repetitions'], 0)
        self.assertFalse(summary.iloc[0]['all_sustainable'])
        self.assertTrue(pd.isna(summary.iloc[0].get('achieved_rps')))

    def test_changed_raw_evidence_fails_closed(self):
        self.write_record()
        with (self.run / 'k6.log').open('a') as stream:
            stream.write('changed')
        with self.assertRaisesRegex(RuntimeError, 'evidence changed'):
            reviewed_rejection(self.run, self.row)

    def test_record_for_another_attempt_is_refused(self):
        self.record['relative_source'] = 'fastapi/compute/500-rps/run-2'
        self.write_record()
        with self.assertRaisesRegex(RuntimeError, 'Invalid timing investigation'):
            reviewed_rejection(self.run, self.row)

    def test_performance_failures_and_other_corruption_are_not_overridden(self):
        self.write_record()
        for state in ('PASS', 'ERROR_LIMIT', 'LATENCY_LIMIT', 'OUT_OF_MEMORY'):
            self.assertIsNone(reviewed_rejection(self.run, {**self.row, 'state': state}))
        self.assertIsNone(reviewed_rejection(self.run, {**self.row, 'reason': 'Missing resource samples'}))


if __name__ == '__main__':
    unittest.main()
