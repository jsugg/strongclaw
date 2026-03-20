#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
RUNS_DIR="${RUNS_DIR:-$(strongclaw_runs_dir)/nightly}"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to run nightly validation." >&2
  exit 1
}

cd "$ROOT"
: "${CLAWOPS_HTTP_RETRY_MODE:=safe}"
export CLAWOPS_HTTP_RETRY_MODE
PYTHONPATH=src uv run pytest -q --cov=src/clawops --cov-report=xml
PYTHONPATH=src uv run python -m compileall -q src tests
"$ROOT/scripts/bootstrap/run_harness_smoke.sh" "$RUNS_DIR"
PYTHONPATH=src uv run python -m clawops context index --config platform/configs/context/context-service.yaml --repo . --json
PYTHONPATH=src uv run python -m clawops workflow --workflow platform/configs/workflows/daily_healthcheck.yaml --dry-run
