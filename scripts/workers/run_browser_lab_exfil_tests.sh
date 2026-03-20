#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
export_strongclaw_compose_state_dir

echo "Testing allowed domain via browser-lab proxy..."
docker compose -f "$ROOT/platform/compose/docker-compose.browser-lab.yaml" exec -T browserlab-playwright \
  bash -lc 'curl -I -sS https://github.com >/dev/null'

echo "Testing blocked domain via browser-lab proxy (should fail)..."
if docker compose -f "$ROOT/platform/compose/docker-compose.browser-lab.yaml" exec -T browserlab-playwright \
  bash -lc 'curl -I -sS https://example.org >/dev/null'; then
  echo "Blocked-domain test failed: proxy allowed unexpected host."
  exit 1
fi

echo "Browser-lab exfil tests completed."
