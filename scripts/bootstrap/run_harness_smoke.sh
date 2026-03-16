#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/.runs}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="$ROOT/${OUTPUT_DIR#./}"
fi

cd "$ROOT"
mkdir -p "$OUTPUT_DIR"

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops harness \
  --suite "$ROOT/platform/configs/harness/security_regressions.yaml" \
  --output "$OUTPUT_DIR/security.jsonl"

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m clawops harness \
  --suite "$ROOT/platform/configs/harness/policy_regressions.yaml" \
  --output "$OUTPUT_DIR/policy.jsonl"
