#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PYTHONPATH=src uv run --project "$ROOT" --locked --extra dev pytest -q \
  "$ROOT/tests/test_hypermemory.py" \
  "$ROOT/tests/test_hypermemory_qdrant_backend.py" \
  "$ROOT/tests/test_hypermemory_qdrant_integration.py"
