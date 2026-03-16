#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
./scripts/ops/launch_sidecars_with_varlock.sh
docker compose -f platform/compose/docker-compose.aux-stack.yaml ps
