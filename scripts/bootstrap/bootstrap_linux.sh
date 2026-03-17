#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
ACPX_VERSION="${ACPX_VERSION:-0.3.0}"

sudo apt-get update
sudo apt-get install -y python3 python3-pip jq sqlite3 nodejs npm docker.io docker-compose-plugin curl unzip
if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
fi
export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
export PATH="$BUN_INSTALL/bin:$PATH"
python3 -m pip install -e "$ROOT"
sudo npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"
mkdir -p "$HOME/.openclaw/clawops" "$HOME/.openclaw/logs" "$ROOT/platform/compose/state"
command -v openclaw >/dev/null 2>&1 || { echo "openclaw install failed"; exit 1; }
command -v acpx >/dev/null 2>&1 || { echo "acpx install failed"; exit 1; }
"$ROOT/scripts/bootstrap/render_openclaw_config.sh"
echo "Linux bootstrap complete."
