#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/.runs}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="$ROOT/${OUTPUT_DIR#./}"
fi

cd "$ROOT"
mkdir -p "$OUTPUT_DIR"

run_clawops "$ROOT" harness \
  --suite "$ROOT/platform/configs/harness/security_regressions.yaml" \
  --output "$OUTPUT_DIR/security.jsonl"

run_clawops "$ROOT" harness \
  --suite "$ROOT/platform/configs/harness/policy_regressions.yaml" \
  --output "$OUTPUT_DIR/policy.jsonl"
