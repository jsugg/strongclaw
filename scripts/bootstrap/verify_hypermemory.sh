#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
CONFIG_PATH=""

# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/rendered_openclaw_contract.sh"

usage() {
  cat <<'EOF'
Usage: verify_hypermemory.sh [--config PATH]

Verify the supported sparse+dense hypermemory contract.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --config requires a value." >&2
        exit 1
      fi
      CONFIG_PATH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$CONFIG_PATH" ]]; then
  if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
    echo "ERROR: Rendered OpenClaw config not found at $OPENCLAW_CONFIG." >&2
    exit 1
  fi
  CONFIG_PATH="$(rendered_openclaw_hypermemory_config_path "$OPENCLAW_CONFIG")"
fi

if [[ -z "$CONFIG_PATH" || ! -f "$CONFIG_PATH" ]]; then
  echo "ERROR: Hypermemory config not found at ${CONFIG_PATH:-<empty>}." >&2
  exit 1
fi

run_clawops "$ROOT" hypermemory verify --config "$CONFIG_PATH" --json
