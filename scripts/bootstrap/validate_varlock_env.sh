#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARLOCK_ENV_DIR="${VARLOCK_ENV_DIR:-$ROOT/platform/configs/varlock}"
VARLOCK_LOCAL_ENV_FILE="${VARLOCK_LOCAL_ENV_FILE:-$VARLOCK_ENV_DIR/.env.local}"

require_command() {
  local command_name="$1"
  local message="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

if [[ ! -f "$VARLOCK_LOCAL_ENV_FILE" ]]; then
  echo "ERROR: Varlock local env contract not found at $VARLOCK_LOCAL_ENV_FILE." >&2
  echo "Copy $VARLOCK_ENV_DIR/.env.local.example to $VARLOCK_LOCAL_ENV_FILE and fill the required values." >&2
  exit 1
fi

require_command varlock "Varlock is required to validate the local env contract."

if ! varlock load --path "$VARLOCK_ENV_DIR" >/dev/null; then
  echo "ERROR: Varlock failed to validate the env contract in $VARLOCK_ENV_DIR." >&2
  echo "Run \`varlock load --path $VARLOCK_ENV_DIR\` to inspect the validation errors." >&2
  exit 1
fi

echo "Validated Varlock env contract at $VARLOCK_LOCAL_ENV_FILE"
