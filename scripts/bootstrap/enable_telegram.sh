#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
run_clawops "$ROOT" merge-json \
  --base "$HOME/.openclaw/openclaw.json" \
  --overlay "$ROOT/platform/configs/openclaw/30-channels.json5" \
  --output "$TMP"
mv "$TMP" "$HOME/.openclaw/openclaw.json"
echo "Telegram/WhatsApp channel overlay merged. Configure owner IDs before production use."
echo "Approve pairings with: openclaw pairing list telegram && openclaw pairing approve telegram <CODE>"
