#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
export_strongclaw_compose_state_dir
cd "$ROOT/platform/compose"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"

if varlock_is_available; then
  run_varlock run --path "$ROOT/platform/configs/varlock" -- docker compose -f docker-compose.aux-stack.yaml up -d
else
  docker compose -f docker-compose.aux-stack.yaml up -d
fi
