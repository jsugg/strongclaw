#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$HOME/.acpx"
cp -n "$ROOT/platform/workers/acpx/global-config.example.json" "$HOME/.acpx/config.json" || true
mkdir -p "$ROOT/repo/upstream"
cp -n "$ROOT/platform/workers/acpx/project-config.example.json" "$ROOT/repo/upstream/.acpxrc.json" || true
echo "ACP worker config templates installed."
