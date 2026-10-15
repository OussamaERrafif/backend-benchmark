"""Recognize explicitly investigated rejections without accepting their metrics."""
import hashlib
import json
from pathlib import Path


def investigation_path(directory):
    directory = Path(directory).resolve()
    session = next(parent for parent in directory.parents if (parent / 'session.json').exists())
    relative = directory.relative_to(session).as_posix()
    key = hashlib.sha256(relative.encode()).hexdigest()
    return session / 'investigations' / (key + '.json'), relative


def reviewed_rejection(directory, validation):
    if not (validation.get('rejected') and validation.get('state') == 'BENCHMARK_ERROR'
            and validation.get('reason') == 'Invalid scenario measurement window'):
        return None
    directory = Path(directory).resolve()
    path, relative = investigation_path(directory)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if (record.get('relative_source') != relative
            or record.get('resolution') != 'retain_rejected_as_failed_load'
            or record.get('validation_reason') != validation['reason']):
        raise RuntimeError('Invalid timing investigation record: ' + str(path))
    current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in directory.iterdir() if p.is_file()}
    if not current or current != record.get('raw_sha256'):
        raise RuntimeError('Investigated attempt evidence changed: ' + str(directory))
    return record
