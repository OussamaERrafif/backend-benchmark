#!/usr/bin/env bash
set -euo pipefail
exec bash "$(dirname "$0")/invoke.sh" benchmark-all "${1:-small}"
