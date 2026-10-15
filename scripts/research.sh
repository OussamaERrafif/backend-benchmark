#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f verification/research-campaign.json ]] && grep -Eq '"state"[[:space:]]*:[[:space:]]*"COMPLETE"' verification/research-campaign.json; then
    echo 'Full research is complete: reports/benchmark-report.html'
    exit 0
fi
docker compose up -d --no-build research
docker update --restart unless-stopped backend-benchmark-research
echo 'Full standard research started; checkpoints and reports update automatically.'
echo 'Read progress: docker logs --tail 30 backend-benchmark-research'
