#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$ROOT/platform/plugins/strongclaw-memory-v2"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"

command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "uv is required" >&2; exit 1; }

tool_dir="$(mktemp -d "${TMPDIR:-/tmp}/strongclaw-openclaw-cli.XXXXXX")"
trap 'node -e "require(\"node:fs\").rmSync(process.argv[1], { recursive: true, force: true })" "$tool_dir"' EXIT

npm install --prefix "$tool_dir" --no-fund --no-audit "openclaw@${OPENCLAW_VERSION}"
export PATH="$tool_dir/node_modules/.bin:$PATH"
cd "$PLUGIN_DIR"
npm run test:openclaw-host
