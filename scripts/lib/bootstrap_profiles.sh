#!/usr/bin/env bash

set -euo pipefail

resolve_bootstrap_profile() {
  local profile="${1:-${OPENCLAW_CONFIG_PROFILE:-default}}"
  if [[ -z "$profile" ]]; then
    profile="default"
  fi
  printf '%s\n' "$profile"
}

profile_requires_qmd() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    default | memory-pro-local | memory-pro-local-smart | acp | browser-lab) return 0 ;;
    *) return 1 ;;
  esac
}

profile_requires_lossless_claw() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    lossless-hypermemory-tier1) return 0 ;;
    *) return 1 ;;
  esac
}

profile_requires_memory_v2_tier1_backend() {
  local profile
  profile="$(resolve_bootstrap_profile "${1:-}")"
  case "$profile" in
    lossless-hypermemory-tier1) return 0 ;;
    *) return 1 ;;
  esac
}

profile_bootstrap_capabilities() {
  local profile
  local -a capabilities=()
  profile="$(resolve_bootstrap_profile "${1:-}")"
  capabilities+=("memory-plugin")
  if profile_requires_qmd "$profile"; then
    capabilities+=("qmd")
  fi
  if profile_requires_lossless_claw "$profile"; then
    capabilities+=("lossless-claw")
  fi
  if profile_requires_memory_v2_tier1_backend "$profile"; then
    capabilities+=("memory-v2-tier1")
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
