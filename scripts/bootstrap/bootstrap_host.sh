#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BOOTSTRAP_SCRIPT="$(
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m clawops.platform_compat --field bootstrap_script
)"

exec "$ROOT/scripts/bootstrap/$BOOTSTRAP_SCRIPT" "$@"
