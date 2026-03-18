#!/usr/bin/env bash
set -euo pipefail

require_command() {
  local command_name="$1"
  local message="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

HOST_OS="$(uname -s)"
case "$HOST_OS" in
  Darwin)
    require_command brew "Homebrew is required for macOS bootstrap."
    brew --version >/dev/null
    echo "macOS preflight complete."
    ;;
  Linux)
    require_command sudo "sudo is required for Linux bootstrap."
    require_command apt-get "apt-get is required for Linux bootstrap."
    require_command curl "curl is required for Linux bootstrap."
    echo "Linux preflight complete."
    ;;
  *)
    echo "unsupported host OS for preflight: $HOST_OS" >&2
    exit 1
    ;;
esac
