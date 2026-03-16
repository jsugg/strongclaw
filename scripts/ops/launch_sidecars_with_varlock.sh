#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/platform/compose"

if command -v varlock >/dev/null 2>&1; then
  varlock run --path "$ROOT/platform/configs/varlock" -- docker compose -f docker-compose.aux-stack.yaml up -d
else
  docker compose -f docker-compose.aux-stack.yaml up -d
fi
