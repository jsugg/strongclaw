#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to build release artifacts." >&2
  exit 1
}

cd "$ROOT"
rm -rf build dist
uv run python -m build
