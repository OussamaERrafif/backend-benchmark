#!/usr/bin/env bash
set -euo pipefail
exec bash "$(dirname "$0")/invoke.sh" benchmark "${1:?Specify framework}" "${2:-small}"
