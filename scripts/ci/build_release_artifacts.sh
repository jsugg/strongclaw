#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to build release artifacts." >&2
  exit 1
}

cd "$ROOT"
python3 - <<'PY'
from pathlib import Path
import shutil

for path in (Path("build"), Path("dist")):
    shutil.rmtree(path, ignore_errors=True)
PY

uv run python -m build
