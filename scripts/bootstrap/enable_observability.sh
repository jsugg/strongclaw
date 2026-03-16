#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp)"
clawops merge-json \
  --base "$HOME/.openclaw/openclaw.json" \
  --overlay "$ROOT/platform/configs/openclaw/50-observability.json5" \
  --output "$TMP"
mv "$TMP" "$HOME/.openclaw/openclaw.json"
docker compose -f "$ROOT/platform/compose/docker-compose.aux-stack.yaml" restart otel-collector
echo "Observability overlay merged."
