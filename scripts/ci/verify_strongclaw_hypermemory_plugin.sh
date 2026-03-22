#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$ROOT/platform/plugins/strongclaw-hypermemory"

command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "uv is required" >&2; exit 1; }

cd "$PLUGIN_DIR"
npm run test:openclaw-host
