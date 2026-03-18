#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to sync the locked CI environment." >&2
  exit 1
}

cd "$ROOT"
uv sync --locked --extra dev
