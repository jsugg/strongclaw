#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$ROOT/platform/compose/state/browser-lab"
docker compose -f "$ROOT/platform/compose/docker-compose.browser-lab.yaml" up -d
echo "Browser lab started. Run ./scripts/workers/run_browser_lab_exfil_tests.sh next."
