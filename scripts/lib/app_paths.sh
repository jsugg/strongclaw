#!/usr/bin/env bash

set -euo pipefail

_strongclaw_expand_user_path() {
  local raw_path="${1:-}"
  case "$raw_path" in
    "~") printf '%s\n' "$HOME" ;;
    \~/*) printf '%s/%s\n' "$HOME" "${raw_path#~/}" ;;
    *) printf '%s\n' "$raw_path" ;;
  esac
}

strongclaw_data_dir() {
  if [[ -n "${STRONGCLAW_DATA_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_DATA_DIR"
    return 0
  fi
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    printf '%s/strongclaw\n' "$(_strongclaw_expand_user_path "$XDG_DATA_HOME")"
    return 0
  fi
  case "$(uname -s)" in
    Darwin) printf '%s\n' "$HOME/Library/Application Support/StrongClaw" ;;
    *) printf '%s\n' "$HOME/.local/share/strongclaw" ;;
  esac
}

strongclaw_state_dir() {
  if [[ -n "${STRONGCLAW_STATE_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_STATE_DIR"
    return 0
  fi
  if [[ -n "${XDG_STATE_HOME:-}" ]]; then
    printf '%s/strongclaw\n' "$(_strongclaw_expand_user_path "$XDG_STATE_HOME")"
    return 0
  fi
  case "$(uname -s)" in
    Darwin) printf '%s\n' "$HOME/Library/Application Support/StrongClaw/state" ;;
    *) printf '%s\n' "$HOME/.local/state/strongclaw" ;;
  esac
}

strongclaw_log_dir() {
  if [[ -n "${STRONGCLAW_LOG_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_LOG_DIR"
    return 0
  fi
  case "$(uname -s)" in
    Darwin) printf '%s\n' "$HOME/Library/Logs/StrongClaw" ;;
    *) printf '%s/logs\n' "$(strongclaw_state_dir)" ;;
  esac
}

strongclaw_runs_dir() {
  if [[ -n "${STRONGCLAW_RUNS_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_RUNS_DIR"
    return 0
  fi
  printf '%s/runs\n' "$(strongclaw_state_dir)"
}

strongclaw_compose_state_dir() {
  if [[ -n "${STRONGCLAW_COMPOSE_STATE_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_COMPOSE_STATE_DIR"
    return 0
  fi
  printf '%s/compose\n' "$(strongclaw_state_dir)"
}

strongclaw_lossless_claw_dir() {
  if [[ -n "${LOSSLESS_CLAW_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$LOSSLESS_CLAW_DIR"
    return 0
  fi
  if [[ -n "${STRONGCLAW_LOSSLESS_CLAW_DIR:-}" ]]; then
    _strongclaw_expand_user_path "$STRONGCLAW_LOSSLESS_CLAW_DIR"
    return 0
  fi
  printf '%s/plugins/lossless-claw\n' "$(strongclaw_data_dir)"
}

strongclaw_qmd_install_dir() {
  if [[ -n "${QMD_INSTALL_PREFIX:-}" ]]; then
    _strongclaw_expand_user_path "$QMD_INSTALL_PREFIX"
    return 0
  fi
  printf '%s/qmd\n' "$(strongclaw_data_dir)"
}

export_strongclaw_compose_state_dir() {
  local compose_state_dir
  compose_state_dir="$(strongclaw_compose_state_dir)"
  mkdir -p "$compose_state_dir"
  export STRONGCLAW_COMPOSE_STATE_DIR="$compose_state_dir"
}
