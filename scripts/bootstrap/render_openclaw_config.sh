#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BASE="$ROOT/platform/configs/openclaw/00-baseline.json5"
TRUST_ZONES_OVERLAY="$ROOT/platform/configs/openclaw/10-trust-zones.json5"
QMD_TEMPLATE="$ROOT/platform/configs/openclaw/40-qmd-context.json5"
OUT="$HOME/.openclaw/openclaw.json"
QMD_OVERLAY="$(mktemp "${TMPDIR:-/tmp}/strongclaw-qmd-overlay.XXXXXX")"

trap 'rm -f "$QMD_OVERLAY"' EXIT

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops.openclaw_config \
  --template "$QMD_TEMPLATE" \
  --repo-root "$ROOT" \
  --home-dir "$HOME" \
  --output "$QMD_OVERLAY"

mkdir -p "$(dirname "$OUT")"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops merge-json \
  --base "$BASE" \
  --overlay "$TRUST_ZONES_OVERLAY" "$QMD_OVERLAY" \
  --output "$OUT"
chmod 600 "$OUT"
echo "Rendered $OUT"
