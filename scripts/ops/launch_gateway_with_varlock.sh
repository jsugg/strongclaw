#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_PATH="$ROOT/platform/configs/varlock"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

require_openclaw "Gateway launch requires the OpenClaw CLI."
prepend_clawops_venv_path "$ROOT"

if varlock_is_available; then
  run_varlock run --path "$ENV_PATH" -- openclaw gateway
else
  openclaw gateway
fi
