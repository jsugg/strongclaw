#!/usr/bin/env bash

set -euo pipefail

OPENCLAW_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_PROJECT_ROOT="$(cd "$OPENCLAW_LIB_DIR/../.." && pwd)"

openclaw_is_available() {
  command -v openclaw >/dev/null 2>&1
}

_openclaw_install_hint() {
  case "$(uname -s)" in
    Darwin)
      printf 'Run %s/scripts/bootstrap/bootstrap_macos.sh to attempt installation.' "$OPENCLAW_PROJECT_ROOT"
      ;;
    Linux)
      printf 'Run %s/scripts/bootstrap/bootstrap_linux.sh to attempt installation.' "$OPENCLAW_PROJECT_ROOT"
      ;;
    *)
      printf 'Run the appropriate bootstrap script under %s/scripts/bootstrap/.' "$OPENCLAW_PROJECT_ROOT"
      ;;
  esac
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
