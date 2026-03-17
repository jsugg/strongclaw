#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PREFLIGHT_SCRIPT="$(
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m clawops.platform_compat --field preflight_script
)"

exec "$ROOT/scripts/bootstrap/$PREFLIGHT_SCRIPT" "$@"
