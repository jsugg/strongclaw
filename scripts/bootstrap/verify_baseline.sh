#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERIFY_OPENCLAW_MODELS_SCRIPT="${VERIFY_OPENCLAW_MODELS_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

require_openclaw "Baseline verification runs OpenClaw diagnostics and audits."

QMD_BIN="$HOME/.bun/bin/qmd"
if [[ ! -x "$QMD_BIN" ]]; then
  echo "ERROR: Baseline verification requires the QMD semantic memory backend at $QMD_BIN. Run $ROOT/scripts/bootstrap/bootstrap_qmd.sh." >&2
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
