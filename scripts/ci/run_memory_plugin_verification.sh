#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$ROOT/platform/plugins/memory-lancedb-pro"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"

if [[ "$UNAME_S" == "Darwin" && "$UNAME_M" == "x86_64" ]]; then
  echo "ERROR: memory-lancedb-pro verification requires Linux or Apple Silicon because LanceDB 0.26.2 does not publish a darwin-x64 native binary." >&2
  exit 1
fi

command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }

tool_dir="$(mktemp -d "${TMPDIR:-/tmp}/strongclaw-openclaw-cli.XXXXXX")"
trap 'node -e "require(\"node:fs\").rmSync(process.argv[1], { recursive: true, force: true })" "$tool_dir"' EXIT

cd "$PLUGIN_DIR"
npm ci
npm test
npm install --prefix "$tool_dir" --no-fund --no-audit "openclaw@${OPENCLAW_VERSION}"
export PATH="$tool_dir/node_modules/.bin:$PATH"
npm run test:openclaw-host
