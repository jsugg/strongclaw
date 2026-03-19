#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOSSLESS_CLAW_REPO="${LOSSLESS_CLAW_REPO:-https://github.com/Martian-Engineering/lossless-claw.git}"
LOSSLESS_CLAW_REF="${LOSSLESS_CLAW_REF:-v0.3.0}"
LOSSLESS_CLAW_DIR="${LOSSLESS_CLAW_DIR:-$ROOT/vendor/lossless-claw}"

command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }

plugin_is_healthy() {
  [[ -f "$LOSSLESS_CLAW_DIR/openclaw.plugin.json" ]] || return 1
  [[ -f "$LOSSLESS_CLAW_DIR/package.json" ]] || return 1
  [[ -d "$LOSSLESS_CLAW_DIR/node_modules" ]] || return 1
  npm --prefix "$LOSSLESS_CLAW_DIR" ls --omit=dev >/dev/null 2>&1
}

install_lossless_claw() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  git clone --depth=1 --branch "$LOSSLESS_CLAW_REF" "$LOSSLESS_CLAW_REPO" "$temp_dir/source" >/dev/null 2>&1
  rm -rf "$LOSSLESS_CLAW_DIR"
  mkdir -p "$(dirname "$LOSSLESS_CLAW_DIR")"
  cp -R "$temp_dir/source" "$LOSSLESS_CLAW_DIR"
  rm -rf "$LOSSLESS_CLAW_DIR/.git"
  npm ci --prefix "$LOSSLESS_CLAW_DIR" --omit=dev --no-fund --no-audit >/dev/null
}

if plugin_is_healthy; then
  echo "lossless-claw already installed at $LOSSLESS_CLAW_DIR"
  exit 0
fi

echo "Installing lossless-claw ${LOSSLESS_CLAW_REF} into $LOSSLESS_CLAW_DIR"
install_lossless_claw
echo "lossless-claw installed at $LOSSLESS_CLAW_DIR"
