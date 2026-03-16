#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BASE="$ROOT/platform/configs/openclaw/00-baseline.json5"
TRUST_ZONES_OVERLAY="$ROOT/platform/configs/openclaw/10-trust-zones.json5"
QMD_TEMPLATE="$ROOT/platform/configs/openclaw/40-qmd-context.json5"
OUT="$HOME/.openclaw/openclaw.json"
RENDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/strongclaw-openclaw-render.XXXXXX")"
BASE_RENDERED="$RENDER_DIR/00-baseline.json"
TRUST_RENDERED="$RENDER_DIR/10-trust-zones.json"
QMD_RENDERED="$RENDER_DIR/40-qmd-context.json"
USER_TIMEZONE="${OPENCLAW_USER_TIMEZONE:-${TZ:-}}"

trap 'rm -f "$BASE_RENDERED" "$TRUST_RENDERED" "$QMD_RENDERED"; rmdir "$RENDER_DIR"' EXIT

render_overlay() {
  local template="$1"
  local output="$2"
  local args=(
    --template "$template"
    --repo-root "$ROOT"
    --home-dir "$HOME"
    --output "$output"
  )
  if [[ -n "$USER_TIMEZONE" ]]; then
    args+=(--user-timezone "$USER_TIMEZONE")
  fi
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops.openclaw_config "${args[@]}"
}

render_overlay "$BASE" "$BASE_RENDERED"
render_overlay "$TRUST_ZONES_OVERLAY" "$TRUST_RENDERED"
render_overlay "$QMD_TEMPLATE" "$QMD_RENDERED"

mkdir -p "$(dirname "$OUT")"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops merge-json \
  --base "$BASE_RENDERED" \
  --overlay "$TRUST_RENDERED" "$QMD_RENDERED" \
  --output "$OUT"
chmod 600 "$OUT"
echo "Rendered $OUT"
