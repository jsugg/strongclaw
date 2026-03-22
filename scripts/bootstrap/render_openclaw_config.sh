#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USER_TIMEZONE="${OPENCLAW_USER_TIMEZONE:-${TZ:-}}"
DEFAULT_PROFILE="${OPENCLAW_CONFIG_PROFILE:-${STRONGCLAW_DEFAULT_PROFILE:-hypermemory}}"

args=(
  render-openclaw-config
  --repo-root "$ROOT"
  --home-dir "$HOME"
)
if [[ $# -eq 0 ]]; then
  args+=(--profile "$DEFAULT_PROFILE")
fi
if [[ -n "$USER_TIMEZONE" ]]; then
  args+=(--user-timezone "$USER_TIMEZONE")
fi

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops "${args[@]}" "$@"
