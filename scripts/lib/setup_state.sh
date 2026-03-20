#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "${BASH_SOURCE[0]%/*}" && pwd)/app_paths.sh"

OPENCLAW_SETUP_STATE_DIR="${OPENCLAW_SETUP_STATE_DIR:-$(strongclaw_state_dir)/setup}"
OPENCLAW_BOOTSTRAP_STATE_FILE="${OPENCLAW_BOOTSTRAP_STATE_FILE:-$OPENCLAW_SETUP_STATE_DIR/bootstrap.env}"
OPENCLAW_DOCKER_REFRESH_STATE_FILE="${OPENCLAW_DOCKER_REFRESH_STATE_FILE:-$OPENCLAW_SETUP_STATE_DIR/docker-refresh.env}"

ensure_setup_state_dir() {
  mkdir -p "$OPENCLAW_SETUP_STATE_DIR"
}

_write_setup_state() {
  local target_path="$1"
  shift

  ensure_setup_state_dir
  : >"$target_path"
  while [[ $# -gt 0 ]]; do
    printf '%s=%s\n' "$1" "$2" >>"$target_path"
    shift 2
  done
}

setup_state_value() {
  local target_path="$1"
  local key="$2"
  if [[ ! -f "$target_path" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$target_path" | tail -n 1 | cut -d= -f2- || true
}

bootstrap_state_ready() {
  [[ -f "$OPENCLAW_BOOTSTRAP_STATE_FILE" ]]
}

mark_bootstrap_complete() {
  local profile="$1"
  local host_os="$2"
  local runtime_user="$3"

  _write_setup_state \
    "$OPENCLAW_BOOTSTRAP_STATE_FILE" \
    PROFILE "$profile" \
    HOST_OS "$host_os" \
    RUNTIME_USER "$runtime_user" \
    COMPLETED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

docker_shell_refresh_required() {
  [[ -f "$OPENCLAW_DOCKER_REFRESH_STATE_FILE" ]]
}

mark_docker_shell_refresh_required() {
  local runtime_user="$1"
  local reason="$2"

  _write_setup_state \
    "$OPENCLAW_DOCKER_REFRESH_STATE_FILE" \
    RUNTIME_USER "$runtime_user" \
    REASON "$reason" \
    CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

clear_docker_shell_refresh_required() {
  rm -f "$OPENCLAW_DOCKER_REFRESH_STATE_FILE"
}
