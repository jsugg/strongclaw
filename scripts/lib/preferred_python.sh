#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -n "${STRONGCLAW_PREFERRED_PYTHON:-}" ]]; then
  printf '%s\n' "$STRONGCLAW_PREFERRED_PYTHON"
  exit 0
fi

if command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ -f "$ROOT/src/clawops/platform_compat.py" ]]; then
  preferred_python="$(
    PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m clawops.platform_compat --field preferred_project_python_version \
      2>/dev/null || true
  )"
  if [[ -n "$preferred_python" && "$preferred_python" != "null" ]]; then
    printf '%s\n' "$preferred_python"
    exit 0
  fi
fi

os_name="$(uname -s)"
architecture="$(uname -m)"
case "$architecture" in
  amd64)
    architecture="x86_64"
    ;;
  aarch64)
    architecture="arm64"
    ;;
esac

if [[ "$os_name" == "Darwin" || "$os_name" == "Linux" ]]; then
  case "$architecture" in
    x86_64|arm64)
      printf '3.12\n'
      ;;
  esac
fi
