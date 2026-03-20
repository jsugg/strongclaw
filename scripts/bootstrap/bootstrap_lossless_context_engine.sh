#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOSSLESS_CLAW_REPO="${LOSSLESS_CLAW_REPO:-https://github.com/Martian-Engineering/lossless-claw.git}"
LOSSLESS_CLAW_REF="${LOSSLESS_CLAW_REF:-v0.3.0}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
LOSSLESS_CLAW_DIR="${LOSSLESS_CLAW_DIR:-$(strongclaw_lossless_claw_dir)}"
LOSSLESS_CLAW_REF_MARKER="$LOSSLESS_CLAW_DIR/.strongclaw-lossless-ref"

command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }

plugin_is_healthy() {
  [[ -f "$LOSSLESS_CLAW_DIR/openclaw.plugin.json" ]] || return 1
  [[ -f "$LOSSLESS_CLAW_DIR/package.json" ]] || return 1
  [[ -d "$LOSSLESS_CLAW_DIR/node_modules" ]] || return 1
  [[ -f "$LOSSLESS_CLAW_REF_MARKER" ]] || return 1
  [[ "$(cat "$LOSSLESS_CLAW_REF_MARKER")" == "$LOSSLESS_CLAW_REF" ]] || return 1
  npm --prefix "$LOSSLESS_CLAW_DIR" ls --omit=dev >/dev/null 2>&1
}

plugin_sources_are_present() {
  [[ -f "$LOSSLESS_CLAW_DIR/openclaw.plugin.json" ]] || return 1
  [[ -f "$LOSSLESS_CLAW_DIR/package.json" ]] || return 1
}

sync_lossless_claw_checkout() {
  if [[ -d "$LOSSLESS_CLAW_DIR/.git" ]]; then
    git -C "$LOSSLESS_CLAW_DIR" fetch --depth=1 origin "$LOSSLESS_CLAW_REF" >/dev/null 2>&1
    git -C "$LOSSLESS_CLAW_DIR" checkout --force FETCH_HEAD >/dev/null 2>&1
    return 0
  fi

  if [[ -d "$LOSSLESS_CLAW_DIR" ]]; then
    if ! plugin_sources_are_present; then
      echo "ERROR: $LOSSLESS_CLAW_DIR exists but is not a lossless-claw checkout." >&2
      echo "Move it aside or set LOSSLESS_CLAW_DIR to an empty path, then rerun this script." >&2
      exit 1
    fi
    return 0
  fi

  mkdir -p "$(dirname "$LOSSLESS_CLAW_DIR")"
  git clone --depth=1 --branch "$LOSSLESS_CLAW_REF" "$LOSSLESS_CLAW_REPO" "$LOSSLESS_CLAW_DIR" >/dev/null 2>&1
}

install_lossless_claw() {
  sync_lossless_claw_checkout
  npm ci --prefix "$LOSSLESS_CLAW_DIR" --omit=dev --no-fund --no-audit >/dev/null
  printf '%s\n' "$LOSSLESS_CLAW_REF" >"$LOSSLESS_CLAW_REF_MARKER"
}

if plugin_is_healthy; then
  echo "lossless-claw already installed at $LOSSLESS_CLAW_DIR"
  exit 0
fi

echo "Installing lossless-claw ${LOSSLESS_CLAW_REF} into $LOSSLESS_CLAW_DIR"
install_lossless_claw
echo "lossless-claw installed at $LOSSLESS_CLAW_DIR"
