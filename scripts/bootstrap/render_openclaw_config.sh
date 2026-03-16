#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="$ROOT/platform/configs/openclaw/00-baseline.json5"
OVERLAY="$ROOT/platform/configs/openclaw/10-trust-zones.json5"
OUT="$HOME/.openclaw/openclaw.json"

mkdir -p "$(dirname "$OUT")"
clawops merge-json --base "$BASE" --overlay "$OVERLAY" --output "$OUT"
chmod 600 "$OUT"
echo "Rendered $OUT"
