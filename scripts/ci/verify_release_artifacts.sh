#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${1:-$ROOT/dist}"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to verify release artifacts." >&2
  exit 1
}

cd "$ROOT"
uv run twine check "$DIST_DIR"/*

wheel_path="$(find "$DIST_DIR" -maxdepth 1 -name '*.whl' -print -quit)"
sdist_path="$(find "$DIST_DIR" -maxdepth 1 -name '*.tar.gz' -print -quit)"

if [[ -z "$wheel_path" || -z "$sdist_path" ]]; then
  echo "Both a wheel and an sdist are required for release verification." >&2
  exit 1
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/strongclaw-release-verify.XXXXXX")"

cleanup_tmp_dir() {
  python3 - "$tmp_dir" <<'PY'
import shutil
import sys

shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
}

trap cleanup_tmp_dir EXIT

verify_install() {
  local artifact_path="$1"
  local env_dir="$2"
  local python_bin
  local entrypoint

  python3 -m venv "$env_dir"
  python_bin="$env_dir/bin/python"
  entrypoint="$env_dir/bin/clawops"
  "$python_bin" -m pip install --upgrade pip
  "$python_bin" -m pip install "$artifact_path"
  [[ -x "$entrypoint" ]] || {
    echo "clawops entrypoint missing after installing $artifact_path" >&2
    exit 1
  }
  "$python_bin" - <<'PY'
import importlib.metadata as metadata
import clawops

assert metadata.version("clawops")
assert clawops.__file__
PY
}

verify_install "$wheel_path" "$tmp_dir/wheel-env"
verify_install "$sdist_path" "$tmp_dir/sdist-env"
