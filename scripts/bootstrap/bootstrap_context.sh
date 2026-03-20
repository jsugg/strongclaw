#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
run_clawops "$ROOT" context index --config "$ROOT/platform/configs/context/context-service.yaml" --repo "$ROOT"
