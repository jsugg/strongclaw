#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v brew >/dev/null 2>&1 || { echo "Homebrew is required"; exit 1; }
brew install jq sqlite python || true
brew install node || true
brew install dmno-dev/tap/varlock || true
brew install bun || true

python3 -m pip install -e "$ROOT"
npm install -g "openclaw@${OPENCLAW_VERSION:-2026.3.13}" || true
npm install -g acpx@latest || true
"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"

mkdir -p "$HOME/.openclaw/clawops" "$HOME/.openclaw/logs" "$ROOT/platform/compose/state"
echo "Bootstrap complete."
