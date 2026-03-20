#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_PARENT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
TMP_ROOT="${SETUP_COMPAT_ROOT:-$TMP_PARENT/strongclaw-setup-compat}"
HOME_DIR="${SETUP_COMPAT_HOME:-$TMP_ROOT/home}"
VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"

mkdir -p "$HOME_DIR"
export HOME="$HOME_DIR"
export STRONGCLAW_DATA_DIR="${STRONGCLAW_DATA_DIR:-$TMP_ROOT/data}"
export STRONGCLAW_STATE_DIR="${STRONGCLAW_STATE_DIR:-$TMP_ROOT/state}"

cd "$ROOT"
"$ROOT/scripts/bootstrap/bootstrap_varlock.sh"
"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
"$ROOT/scripts/bootstrap/bootstrap_lossless_context_engine.sh"
PYTHONPATH=src uv run --project "$ROOT" --locked --extra dev python -m clawops render-openclaw-config \
  --profile lossless-hypermemory-tier1 \
  --repo-root "$ROOT" \
  --output "$TMP_ROOT/openclaw.json"

test -x "$HOME/.bun/bin/qmd"
test -f "$STRONGCLAW_DATA_DIR/plugins/lossless-claw/openclaw.plugin.json"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"
varlock_version_matches "$VARLOCK_VERSION"
