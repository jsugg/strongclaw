#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
ACPX_VERSION="${ACPX_VERSION:-0.3.0}"

"$ROOT/scripts/bootstrap/preflight_macos.sh"

command -v brew >/dev/null 2>&1 || { echo "Homebrew is required"; exit 1; }
brew install jq sqlite python
brew install node
brew install dmno-dev/tap/varlock
brew install bun

python3 -m pip install -e "$ROOT"
npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"

mkdir -p "$HOME/.openclaw/clawops" "$HOME/.openclaw/logs" "$ROOT/platform/compose/state"
command -v openclaw >/dev/null 2>&1 || { echo "openclaw install failed"; exit 1; }
command -v acpx >/dev/null 2>&1 || { echo "acpx install failed"; exit 1; }
"$ROOT/scripts/bootstrap/render_openclaw_config.sh"
"$ROOT/scripts/bootstrap/doctor_host.sh"
echo "Bootstrap complete."
