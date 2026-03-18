#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

require_openclaw "Baseline verification runs OpenClaw diagnostics and audits."

QMD_BIN="$HOME/.bun/bin/qmd"
if [[ ! -x "$QMD_BIN" ]]; then
  echo "ERROR: Baseline verification requires the QMD semantic memory backend at $QMD_BIN. Run $ROOT/scripts/bootstrap/bootstrap_qmd.sh." >&2
  exit 1
fi

echo "== OpenClaw doctor =="
openclaw doctor

echo "== OpenClaw security audit =="
openclaw security audit --deep

echo "== OpenClaw secrets audit =="
openclaw secrets audit --check

echo "== OpenClaw memory status =="
openclaw memory status --deep

echo "== OpenClaw memory search =="
openclaw memory search --query "ClawOps" --max-results 1 >/dev/null

echo "== Python tests =="
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" pytest -q "$ROOT/tests"

echo "== Harness smoke =="
"$ROOT/scripts/bootstrap/run_harness_smoke.sh" "$ROOT/.runs"

echo "== Sidecar static verification =="
"$ROOT/scripts/bootstrap/verify_sidecars.sh" --skip-runtime

echo "== Observability static verification =="
"$ROOT/scripts/bootstrap/verify_observability.sh" --skip-runtime

echo "== Channel verification =="
"$ROOT/scripts/bootstrap/verify_channels.sh"

echo "== Done =="
