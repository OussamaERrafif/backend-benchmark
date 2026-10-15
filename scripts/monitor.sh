#!/usr/bin/env bash
set -euo pipefail
# Standalone helper; the controller records all three roles automatically.
container=${1:?Specify container}
output=${2:?Specify output CSV path}
printf 'timestamp,container,cpu_percent,memory_usage,memory_percent,network_io,block_io,pids\n' > "$output"
while docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | grep -q true; do
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker stats --no-stream --format "$stamp,{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDs}}" "$container" >> "$output"
  sleep 1
done
