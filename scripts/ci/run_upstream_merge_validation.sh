#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

"$ROOT/scripts/ci/run_repository_quality_gate.sh"
"$ROOT/scripts/ci/run_nightly_validation.sh"
