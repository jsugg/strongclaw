#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_PATH="$ROOT/platform/configs/varlock"
# shellcheck source=../lib/openclaw.sh
source "$ROOT/scripts/lib/openclaw.sh"

require_openclaw "Gateway launch requires the OpenClaw CLI."

if command -v varlock >/dev/null 2>&1; then
  varlock run --path "$ENV_PATH" -- openclaw gateway
else
  openclaw gateway
fi
