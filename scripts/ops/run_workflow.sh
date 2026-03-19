#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
WORKFLOW="${1:?workflow required}"
shift || true
if [[ "$WORKFLOW" != /* ]]; then
  WORKFLOW="$ROOT/$WORKFLOW"
fi
run_clawops "$ROOT" workflow --workflow "$WORKFLOW" --base-dir "$ROOT" "$@"
