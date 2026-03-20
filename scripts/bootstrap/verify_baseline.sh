#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERIFY_OPENCLAW_MODELS_SCRIPT="${VERIFY_OPENCLAW_MODELS_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"
VERIFY_MEMORY_V2_TIER1_SCRIPT="${VERIFY_MEMORY_V2_TIER1_SCRIPT:-$ROOT/scripts/bootstrap/verify_memory_v2_tier1.sh}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/rendered_openclaw_contract.sh"

require_openclaw "Baseline verification runs OpenClaw diagnostics and audits."
if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  echo "ERROR: Rendered OpenClaw config not found at $OPENCLAW_CONFIG." >&2
  exit 1
fi

echo "== OpenClaw doctor =="
run_openclaw doctor --non-interactive

echo "== OpenClaw security audit =="
run_openclaw security audit --deep

echo "== OpenClaw secrets audit =="
run_openclaw secrets audit --check

echo "== OpenClaw memory status =="
run_openclaw memory status --deep

echo "== OpenClaw memory search =="
run_openclaw memory search --query "ClawOps" --max-results 1 >/dev/null

echo "== OpenClaw model readiness =="
"$VERIFY_OPENCLAW_MODELS_SCRIPT" --check-only

if rendered_openclaw_uses_memory_v2 "$OPENCLAW_CONFIG"; then
  memory_v2_config_path="$(rendered_openclaw_memory_v2_config_path "$OPENCLAW_CONFIG")"
  if [[ -z "$memory_v2_config_path" || ! -f "$memory_v2_config_path" ]]; then
    echo "ERROR: strongclaw-memory-v2 is enabled, but its configPath is missing or unreadable." >&2
    exit 1
  fi
  echo "== strongclaw-memory-v2 status =="
  memory_v2_status_json="$(run_clawops "$ROOT" memory-v2 status --config "$memory_v2_config_path" --json)"
  printf '%s\n' "$memory_v2_status_json"
  if printf '%s\n' "$memory_v2_status_json" | jq -e '.backendActive == "qdrant_sparse_dense_hybrid"' >/dev/null; then
    echo "== strongclaw-memory-v2 tier-one verification =="
    run_shell_entrypoint "$VERIFY_MEMORY_V2_TIER1_SCRIPT" --config "$memory_v2_config_path"
  fi
fi

echo "== Python tests =="
uv run --project "$ROOT" --locked --extra dev pytest -q "$ROOT/tests"

echo "== Harness smoke =="
"$ROOT/scripts/bootstrap/run_harness_smoke.sh"

echo "== Sidecar static verification =="
"$ROOT/scripts/bootstrap/verify_sidecars.sh" --skip-runtime

echo "== Observability static verification =="
"$ROOT/scripts/bootstrap/verify_observability.sh" --skip-runtime

echo "== Channel verification =="
"$ROOT/scripts/bootstrap/verify_channels.sh"

echo "== Done =="
