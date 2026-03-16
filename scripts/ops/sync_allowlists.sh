#!/usr/bin/env bash
set -euo pipefail

SOURCE="${1:?source yaml/json required}"
OUTPUT="${2:-$HOME/.openclaw/channel-allowlists.json}"

clawops allowlists --source "$SOURCE" --output "$OUTPUT"
echo "Wrote allowlist fragment to $OUTPUT"
