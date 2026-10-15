#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker info --format '{{.ServerVersion}}'
docker compose version
if command -v ss >/dev/null 2>&1 && ss -ltn '( sport = :8080 )' | tail -n +2 | grep -q . && [[ -z "$(docker compose ps --status running -q)" ]]; then
  echo 'Host port 8080 is already occupied.' >&2; exit 1
fi
docker compose build runner
environment_args=()
for name in BENCH_MODE REPETITIONS WARMUP_DURATION BENCH_DURATION RPS_STEPS SCENARIOS MAX_ERROR_RATE MAX_P95_MS MAX_P99_MS MIN_TARGET_ACHIEVEMENT DB_POOL_SIZE RANDOM_SEED COOLDOWN_SECONDS STRESS_DURATION RUN_STRESS PREALLOCATED_VUS MAX_VUS POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  if declare -p "$name" >/dev/null 2>&1; then environment_args+=(-e "$name"); fi
done
if [[ "${1:-}" == build ]]; then
  mkdir .benchmark-lock
  trap 'rmdir .benchmark-lock' EXIT
  docker compose run --rm --no-deps "${environment_args[@]}" runner doctor
  docker compose --profile '*' build
  exit
fi
docker compose run --rm --no-deps "${environment_args[@]}" runner "$@"
