#!/usr/bin/env bash
set -euo pipefail
exec bash "$(dirname "$0")/invoke.sh" doctor "${1:-small}"
