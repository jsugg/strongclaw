#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
export_strongclaw_compose_state_dir
cd "$ROOT"
./scripts/ops/launch_sidecars_with_varlock.sh
docker compose -f platform/compose/docker-compose.aux-stack.yaml ps
