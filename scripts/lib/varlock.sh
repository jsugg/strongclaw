#!/usr/bin/env bash

set -euo pipefail

VARLOCK_BIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/varlock/bin"
LEGACY_VARLOCK_BIN_DIR="$HOME/.varlock/bin"
DEFAULT_VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"

prepend_varlock_path() {
  for candidate in "$VARLOCK_BIN_DIR" "$LEGACY_VARLOCK_BIN_DIR"; do
    if [[ ! -d "$candidate" ]]; then
      continue
    fi
    case ":$PATH:" in
      *":$candidate:"*) ;;
      *) PATH="$candidate:$PATH" ;;
    esac
  done
}

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

current_varlock_version() {
  local varlock_bin
  varlock_bin="$(resolve_varlock_bin)" || return 1
  "$varlock_bin" --version | awk '{print $NF}'
}

varlock_version_matches() {
  local expected_version="${1:-$DEFAULT_VARLOCK_VERSION}"
  local installed_version
  installed_version="$(current_varlock_version 2>/dev/null || true)"
  [[ -n "$installed_version" && "$installed_version" == "$expected_version" ]]
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

ensure_varlock_installed() {
  local expected_version="${1:-$DEFAULT_VARLOCK_VERSION}"
  local install_output
  prepend_varlock_path
  if varlock_version_matches "$expected_version"; then
    return 0
  fi
  if ! install_output="$(
    curl -sSfL https://varlock.dev/install.sh |
      sh -s -- --force-no-brew --version="$expected_version" 2>&1
  )"; then
    printf '%s\n' "$install_output" >&2
    exit 1
  fi
  prepend_varlock_path
  if ! varlock_version_matches "$expected_version"; then
    if [[ -n "$install_output" ]]; then
      printf '%s\n' "$install_output" >&2
    fi
    printf 'ERROR: expected varlock %s, but found %s.\n' \
      "$expected_version" "$(current_varlock_version 2>/dev/null || printf 'unavailable')" >&2
    exit 1
  fi
}
