#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
QMD_BIN="${OPENCLAW_QMD_BIN:-$HOME/.bun/bin/qmd}"
VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"
VERIFY_MEMORY_V2_TIER1_SCRIPT="${VERIFY_MEMORY_V2_TIER1_SCRIPT:-$ROOT/scripts/bootstrap/verify_memory_v2_tier1.sh}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/rendered_openclaw_contract.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"

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
require_varlock "Bootstrap doctor requires the Varlock CLI."
varlock_version_matches "$VARLOCK_VERSION" || {
  echo "ERROR: Bootstrap doctor requires varlock ${VARLOCK_VERSION}. Run $ROOT/scripts/bootstrap/bootstrap_varlock.sh." >&2
  exit 1
}

if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  echo "ERROR: Rendered OpenClaw config not found at $OPENCLAW_CONFIG." >&2
  exit 1
fi

echo "== OpenClaw version =="
openclaw --version

echo "== ACPX version =="
acpx --version

echo "== Varlock version =="
run_varlock --version

echo "== QMD binary =="
printf '%s\n' "$QMD_BIN"

echo "== Rendered config =="
OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" openclaw config validate
jq empty "$OPENCLAW_CONFIG"
printf 'validated %s\n' "$OPENCLAW_CONFIG"

if rendered_openclaw_uses_qmd "$OPENCLAW_CONFIG"; then
  if [[ ! -x "$QMD_BIN" ]]; then
    echo "ERROR: Bootstrap doctor requires the QMD semantic memory backend at $QMD_BIN." >&2
    exit 1
  fi
  echo "== QMD binary =="
  printf '%s\n' "$QMD_BIN"
fi

if rendered_openclaw_uses_lossless_claw "$OPENCLAW_CONFIG"; then
  lossless_plugin_path="$(rendered_openclaw_lossless_plugin_path "$OPENCLAW_CONFIG")"
  if [[ -z "$lossless_plugin_path" || ! -f "$lossless_plugin_path/openclaw.plugin.json" ]]; then
    echo "ERROR: Rendered config enables lossless-claw, but the plugin path is missing or invalid." >&2
    exit 1
  fi
  echo "== Lossless context engine =="
  printf '%s\n' "$lossless_plugin_path"
fi

if rendered_openclaw_uses_memory_v2 "$OPENCLAW_CONFIG"; then
  memory_v2_config_path="$(rendered_openclaw_memory_v2_config_path "$OPENCLAW_CONFIG")"
  if [[ -z "$memory_v2_config_path" || ! -f "$memory_v2_config_path" ]]; then
    echo "ERROR: strongclaw-memory-v2 is enabled, but its configPath is missing or unreadable." >&2
    exit 1
  fi
  echo "== strongclaw-memory-v2 config =="
  printf '%s\n' "$memory_v2_config_path"
  memory_v2_status_json="$(run_clawops "$ROOT" memory-v2 status --config "$memory_v2_config_path" --json)"
  if printf '%s\n' "$memory_v2_status_json" | jq -e '.backendActive == "qdrant_sparse_dense_hybrid"' >/dev/null; then
    run_shell_entrypoint "$VERIFY_MEMORY_V2_TIER1_SCRIPT" --config "$memory_v2_config_path" >/dev/null
  fi
fi
