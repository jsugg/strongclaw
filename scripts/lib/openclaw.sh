#!/usr/bin/env bash

set -euo pipefail

OPENCLAW_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_PROJECT_ROOT="$(cd "$OPENCLAW_LIB_DIR/../.." && pwd)"
OPENCLAW_VARLOCK_ENV_PATH="${OPENCLAW_VARLOCK_ENV_PATH:-$OPENCLAW_PROJECT_ROOT/platform/configs/varlock}"
# shellcheck disable=SC1091
source "$OPENCLAW_LIB_DIR/varlock.sh"

openclaw_is_available() {
  command -v openclaw >/dev/null 2>&1
}

_openclaw_install_hint() {
  printf 'Run %s/scripts/bootstrap/bootstrap.sh to attempt installation.' "$OPENCLAW_PROJECT_ROOT"
}

warn_if_openclaw_missing() {
  local context="${1:-OpenClaw CLI is not installed.}"
  if openclaw_is_available; then
    return 0
  fi
  printf 'WARNING: %s %s\n' "$context" "$(_openclaw_install_hint)" >&2
  return 1
}

require_openclaw() {
  local context="${1:-This task requires the OpenClaw CLI.}"
  if openclaw_is_available; then
    return 0
  fi
  printf 'ERROR: %s %s\n' "$context" "$(_openclaw_install_hint)" >&2
  exit 1
}

run_openclaw() {
  if varlock_is_available && [[ -d "$OPENCLAW_VARLOCK_ENV_PATH" ]]; then
    run_varlock run --path "$OPENCLAW_VARLOCK_ENV_PATH" -- openclaw "$@"
    return 0
  fi
  openclaw "$@"
}
