"""Verify the recorded, semantically identical Caddyfile formatting correction."""
import hashlib
import json
from runner import ROOT


def recorded_caddy_format(session, original_digest, current_digest):
    try:
        record = json.loads((session / 'source-formatting/laravel-caddyfile.json').read_text())
        image = json.loads((session / 'versions.json').read_text())['resolved_images']['laravel']['id']
        if (record['path'] != 'apps/laravel/Caddyfile'
                or record['original_sha256'] != original_digest
                or record['formatted_sha256'] != current_digest
                or record['formatter_image_id'] != image):
            return False
        evidence = ROOT / 'verification/laravel-caddy-format'
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        if digest(evidence / 'original.Caddyfile') != original_digest:
            return False
        if digest(evidence / 'Caddyfile') != current_digest:
            return False
        for workers in (1, 2, 4):
            expected = record['adapted_json_sha256'][str(workers)]
            if (digest(evidence / f'original-{workers}.json') != expected
                    or digest(evidence / f'formatted-{workers}.json') != expected):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
