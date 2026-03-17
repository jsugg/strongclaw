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

require_command brew "Homebrew is required for macOS bootstrap."
brew --version >/dev/null
echo "macOS preflight complete."
