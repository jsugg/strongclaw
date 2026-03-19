#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

run_shell_entrypoint "$ROOT/scripts/bootstrap/configure_varlock_env.sh" --check-only
