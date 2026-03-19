#!/usr/bin/env bash

set -euo pipefail

VARLOCK_BIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/varlock/bin"
LEGACY_VARLOCK_BIN_DIR="$HOME/.varlock/bin"

resolve_varlock_bin() {
  if command -v varlock >/dev/null 2>&1; then
    command -v varlock
    return 0
  fi
  if [[ -x "$VARLOCK_BIN_DIR/varlock" ]]; then
    printf '%s\n' "$VARLOCK_BIN_DIR/varlock"
    return 0
  fi
  if [[ -x "$LEGACY_VARLOCK_BIN_DIR/varlock" ]]; then
    printf '%s\n' "$LEGACY_VARLOCK_BIN_DIR/varlock"
    return 0
  fi
  return 1
}

varlock_is_available() {
  resolve_varlock_bin >/dev/null 2>&1
}

require_varlock() {
  local context="${1:-This task requires the Varlock CLI.}"
  if varlock_is_available; then
    return 0
  fi
  printf 'ERROR: %s Expected Varlock at %s or %s.\n' \
    "$context" "$VARLOCK_BIN_DIR/varlock" "$LEGACY_VARLOCK_BIN_DIR/varlock" >&2
  exit 1
}

run_varlock() {
  local varlock_bin
  varlock_bin="$(resolve_varlock_bin)" || return 1
  "$varlock_bin" "$@"
}
