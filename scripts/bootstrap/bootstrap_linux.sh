#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

sudo apt-get update
sudo apt-get install -y python3 python3-pip jq sqlite3 nodejs npm docker.io docker-compose-plugin curl unzip
if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
fi
export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
export PATH="$BUN_INSTALL/bin:$PATH"
python3 -m pip install -e "$ROOT"
sudo npm install -g "openclaw@${OPENCLAW_VERSION:-2026.3.13}" acpx@latest
"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
echo "Linux bootstrap complete."
