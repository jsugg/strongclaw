#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to run the repository quality gate." >&2
  exit 1
}

cd "$ROOT"
: "${CLAWOPS_HTTP_RETRY_MODE:=safe}"
export CLAWOPS_HTTP_RETRY_MODE
uv run pre-commit run actionlint --all-files
uv run pre-commit run shellcheck --all-files
PYTHONPATH=src uv run pytest -q --cov=src/clawops --cov-report=xml --cov-report=term-missing
PYTHONPATH=src uv run python -m compileall -q src tests
