#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKFLOW="${1:?workflow required}"
shift || true
if [[ "$WORKFLOW" != /* ]]; then
  WORKFLOW="$ROOT/$WORKFLOW"
fi
clawops workflow --workflow "$WORKFLOW" --base-dir "$ROOT" "$@"
