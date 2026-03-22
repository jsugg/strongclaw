#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
export_strongclaw_repo_local_compose_state_dir "$ROOT"

exec "$ROOT/scripts/ops/launch_sidecars_with_varlock.sh" "$@"
