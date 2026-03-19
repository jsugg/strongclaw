#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="${1:?source yaml/json required}"
OUTPUT="${2:-$HOME/.openclaw/channel-allowlists.json}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

run_clawops "$ROOT" allowlists --source "$SOURCE" --output "$OUTPUT"
echo "Wrote allowlist fragment to $OUTPUT"
