#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

exec "$(resolve_clawops_bin "$ROOT")" verify-platform channels --repo-root "$ROOT" "$@"
