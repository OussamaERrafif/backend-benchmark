"""Recovery regressions using the real suspended measurement as a fixture."""
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from runner import ROOT
from recover_suspended import recover_session, suspension_evidence
sys.path.insert(0, str(ROOT / 'analysis'))
from process_results import parse_run


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        campaign = ROOT / 'verification/research-campaign.json'
        if not campaign.exists():
            raise unittest.SkipTest('Requires a research campaign with a preserved suspension fixture')
        sid = json.loads(campaign.read_text())['sessions']['standard']
        cls.session = ROOT / 'results/raw' / sid
        parent = cls.session / 'springboot/plaintext/2000-rps'
        candidates = [parent / 'run-2'] + sorted(parent.glob('aborted-run-2-*'))
        cls.fixture = next((p for p in candidates if (p / 'validation.json').exists()
                            and json.loads((p / 'validation.json').read_text()).get('reason') == 'Invalid scenario measurement window'), None)
        if cls.fixture is None:
            raise unittest.SkipTest('A preserved real suspension fixture is required')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.copy = Path(self.temp.name) / 'session'
        self.copy.mkdir()
        for name in ('session.json', 'versions.json'):
            shutil.copyfile(self.session / name, self.copy / name)
        self.run = self.copy / 'springboot/plaintext/2000-rps/run-2'
        shutil.copytree(self.fixture, self.run)
        (self.run / 'interrupted.json').unlink(missing_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_invalid_window_has_independent_clock_gap(self):
        validation = parse_run(self.run)
        evidence = suspension_evidence(self.run, validation)
        self.assertTrue(validation['rejected'])
        self.assertGreater(evidence['resource_clock_discontinuity']['clock_gap_seconds'], 3000)
        self.assertEqual(evidence['scheduled_seconds'], 120)

    def test_archive_preserves_every_original_byte_and_excludes_numeric_repetition(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.iterdir() if p.is_file()}
        records = recover_session(self.copy, parse_run)
        self.assertEqual(len(records), 1)
        self.assertFalse(self.run.exists())
        archive = Path(records[0]['archive_source'])
        self.assertEqual(before, records[0]['preserved_sha256'])
        for name, digest in before.items():
            self.assertEqual(digest, hashlib.sha256((archive / name).read_bytes()).hexdigest())
        rejected = parse_run(archive)
        self.assertEqual(rejected['kind'], 'interrupted')
        self.assertTrue(rejected['rejected'])
        self.assertNotIn('achieved_rps', rejected)
        self.assertEqual(recover_session(self.copy, parse_run), [])

    def test_real_performance_failures_are_not_retried(self):
        for state in ('LATENCY_LIMIT', 'ERROR_LIMIT', 'THROUGHPUT_LIMIT', 'LOAD_GENERATOR_LIMITED', 'BACKEND_CRASH', 'OUT_OF_MEMORY'):
            self.assertIsNone(suspension_evidence(self.run, {'state': state, 'rejected': state in ('BACKEND_CRASH', 'OUT_OF_MEMORY')}))
        self.assertIsNone(suspension_evidence(self.run, {'state': 'BENCHMARK_ERROR', 'rejected': True, 'reason': 'Missing resource samples for backend'}))

    def test_invalid_window_without_independent_clock_gap_remains_blocked(self):
        stats = self.run / 'docker-stats.csv'
        with stats.open(newline='') as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = list(reader)
        origin = dt.datetime.fromisoformat(rows[0]['timestamp']) - dt.timedelta(seconds=float(rows[0]['elapsed_seconds']))
        for row in rows:
            row['timestamp'] = (origin + dt.timedelta(seconds=float(row['elapsed_seconds']))).isoformat()
        with stats.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        self.assertEqual(recover_session(self.copy, parse_run), [])
        self.assertTrue(self.run.exists())

    def test_valid_measurement_with_wall_clock_correction_is_not_retried(self):
        summary_path = self.run / 'k6-summary.json'
        summary = json.loads(summary_path.read_text())
        window = summary['benchmark_window']
        window['duration_seconds'] = 120
        window['finished_at_ms'] = window['started_at_ms'] + 120000
        summary_path.write_text(json.dumps(summary))
        validation = parse_run(self.run)
        self.assertFalse(validation['rejected'])
        self.assertIsNone(suspension_evidence(self.run, validation))


if __name__ == '__main__':
    unittest.main()
