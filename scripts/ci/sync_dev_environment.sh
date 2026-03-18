#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to sync the locked CI environment." >&2
  exit 1
}

cd "$ROOT"
uv sync --locked --extra dev

if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$ROOT/.venv/bin" >>"$GITHUB_PATH"
fi

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    printf 'VIRTUAL_ENV=%s\n' "$ROOT/.venv"
    printf 'UV_PROJECT_ENVIRONMENT=%s\n' "$ROOT/.venv"
  } >>"$GITHUB_ENV"
fi
