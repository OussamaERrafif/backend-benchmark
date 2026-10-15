"""Preserve and retry validator-rejected attempts with independent clock-gap evidence.

This is orchestration recovery only. The pinned workload, validator, thresholds,
images and accepted measurements are not changed. Performance failures are never
retried by this helper.
"""
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid
from runner import ROOT, save, utc


def suspension_evidence(directory, validation):
    if not (validation.get('rejected') and validation.get('state') == 'BENCHMARK_ERROR'
            and validation.get('reason') == 'Invalid scenario measurement window'):
        return None
    try:
        meta = json.loads((directory / 'metadata.json').read_text())
        summary = json.loads((directory / 'k6-summary.json').read_text())
        window = summary['benchmark_window']
        scheduled = float(meta['duration_seconds'])
        observed = float(window['duration_seconds'])
        if not meta.get('finished_at') or meta.get('throughput_window') != 'scenario-monotonic-v2':
            return None
        if window.get('method') != 'scenario-monotonic-v2' or not all(map(math.isfinite, [scheduled, observed])):
            return None
        if observed <= scheduled + 11 or meta.get('k6_exit_code') not in (0, 99):
            return None
        if not meta.get('backend_healthy') or not meta.get('backend_state', {}).get('Running'):
            return None
        if meta.get('postgres_state', {}).get('Health', {}).get('Status') != 'healthy':
            return None
        if any(meta.get(role + '_state', {}).get('OOMKilled') for role in ('backend', 'postgres', 'k6')):
            return None
        previous = None
        largest = None
        with (directory / 'docker-stats.csv').open(newline='') as stream:
            for sample in csv.DictReader(stream):
                if sample['role'] != 'backend':
                    continue
                wall = dt.datetime.fromisoformat(sample['timestamp']).timestamp()
                monotonic = float(sample['elapsed_seconds'])
                if not all(map(math.isfinite, [wall, monotonic])):
                    return None
                if previous:
                    wall_gap = wall - previous[0]
                    monotonic_gap = monotonic - previous[1]
                    if monotonic_gap < 0:
                        return None
                    difference = wall_gap - monotonic_gap
                    if wall_gap >= 30 and difference >= 30 and (largest is None or difference > largest['clock_gap_seconds']):
                        largest = {'before': previous[2], 'after': sample['timestamp'],
                                   'wall_gap_seconds': wall_gap, 'monotonic_gap_seconds': monotonic_gap,
                                   'clock_gap_seconds': difference}
                previous = (wall, monotonic, sample['timestamp'])
        if largest is None:
            return None
        return {'scheduled_seconds': scheduled, 'observed_window_seconds': observed,
                'validation_reason': validation['reason'], 'resource_clock_discontinuity': largest}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def recover_session(session, parser=None):
    session = Path(session).resolve()
    if parser is None:
        sys.path.insert(0, str(ROOT / 'analysis'))
        from process_results import parse_run
        parser = parse_run
    recovered = []
    candidates = sorted(session.glob('*/*/*/run-*/metadata.json')) + sorted(session.glob('*/*/stress/metadata.json'))
    for metadata in candidates:
        directory = metadata.parent
        if directory.name != 'stress' and not re.fullmatch(r'run-\d+', directory.name):
            continue
        # Completed measurements already have immutable validator evidence. Avoid
        # reparsing hundreds of healthy runs just to locate a rejected attempt.
        try:
            saved = json.loads((directory / 'validation.json').read_text())
        except (OSError, ValueError):
            continue
        if not (saved.get('rejected') and saved.get('state') == 'BENCHMARK_ERROR'
                and saved.get('reason') == 'Invalid scenario measurement window'):
            continue
        validation = parser(directory)
        evidence = suspension_evidence(directory, validation)
        if evidence is None:
            continue
        archive = directory.with_name('aborted-' + directory.name + '-' + uuid.uuid4().hex[:8])
        # Validate both resolved targets before moving any raw evidence.
        if not directory.resolve().is_relative_to(session) or not archive.resolve().is_relative_to(session):
            raise RuntimeError('Suspension archive would leave its research session')
        digests = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in directory.rglob('*') if p.is_file()}
        record = {'timestamp': utc(), 'reason': 'Host suspension or large clock discontinuity invalidated the measurement; retry the same repetition.',
                  'original_source': str(directory), 'archive_source': str(archive),
                  'evidence': evidence, 'preserved_sha256': digests}
        directory.rename(archive)
        for relative, digest in digests.items():
            if hashlib.sha256((archive / relative).read_bytes()).hexdigest() != digest:
                raise RuntimeError('Archived suspension evidence changed: ' + relative)
        save(archive / 'interrupted.json', record)
        save(session / 'recoveries' / (archive.name + '.json'), record)
        recovered.append(record)
        print('Preserved suspended attempt: ' + str(archive) + '; retrying its original repetition.', flush=True)
    return recovered
