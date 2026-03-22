#!/usr/bin/env bash

set -euo pipefail

STRONGCLAW_DEFAULT_PROFILE="${STRONGCLAW_DEFAULT_PROFILE:-hypermemory}"

resolve_bootstrap_profile() {
  local profile="${1:-${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}}"
  if [[ -z "$profile" ]]; then
    profile="$STRONGCLAW_DEFAULT_PROFILE"
  fi
  printf '%s\n' "$profile"
}

profile_requires_qmd() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    openclaw-qmd | memory-pro-local | memory-pro-local-smart | acp | browser-lab) return 0 ;;
    *) return 1 ;;
  esac
}

profile_requires_lossless_claw() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    hypermemory) return 0 ;;
    *) return 1 ;;
  esac
}

profile_requires_hypermemory_backend() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    hypermemory) return 0 ;;
    *) return 1 ;;
  esac
}

profile_requires_memory_pro_plugin() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    memory-pro-local | memory-pro-local-smart) return 0 ;;
    *) return 1 ;;
  esac
}

profile_bootstrap_capabilities() {
  local profile
  local -a capabilities=()
  profile="$(resolve_bootstrap_profile "${1:-}")"
  if profile_requires_qmd "$profile"; then
    capabilities+=("qmd")
  fi
  if profile_requires_memory_pro_plugin "$profile"; then
    capabilities+=("memory-pro-plugin")
  fi
  if profile_requires_lossless_claw "$profile"; then
    capabilities+=("lossless-claw")
  fi
  if profile_requires_hypermemory_backend "$profile"; then
    capabilities+=("hypermemory")
  fi
  printf '%s\n' "${capabilities[*]}"
}

capability_list_contains() {
  local needle="$1"
  shift
  local entry
  for entry in "$@"; do
    if [[ "$entry" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}
