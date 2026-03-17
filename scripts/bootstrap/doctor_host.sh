#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
QMD_BIN="${OPENCLAW_QMD_BIN:-$HOME/.bun/bin/qmd}"
# shellcheck source=../lib/openclaw.sh
source "$ROOT/scripts/lib/openclaw.sh"

require_command() {
  local command_name="$1"
  local message="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

require_openclaw "Bootstrap doctor requires the OpenClaw CLI."
require_command acpx "Bootstrap doctor requires the ACPX CLI."
require_command jq "Bootstrap doctor requires jq to validate the rendered config."

if [[ ! -x "$QMD_BIN" ]]; then
  echo "ERROR: Bootstrap doctor requires the QMD semantic memory backend at $QMD_BIN." >&2
  exit 1
fi

if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  echo "ERROR: Rendered OpenClaw config not found at $OPENCLAW_CONFIG." >&2
  exit 1
fi

echo "== OpenClaw version =="
openclaw --version

echo "== ACPX version =="
acpx --version

echo "== QMD binary =="
printf '%s\n' "$QMD_BIN"

echo "== Rendered config =="
OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" openclaw config validate
jq empty "$OPENCLAW_CONFIG"
printf 'validated %s\n' "$OPENCLAW_CONFIG"
