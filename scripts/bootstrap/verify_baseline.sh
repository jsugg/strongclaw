#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== OpenClaw doctor =="
openclaw doctor

echo "== OpenClaw security audit =="
openclaw security audit --deep

echo "== OpenClaw secrets audit =="
openclaw secrets audit --check

echo "== Python tests =="
PYTHONPATH=src pytest -q

echo "== Harness smoke =="
mkdir -p "$ROOT/.runs"
clawops harness run --suite platform/configs/harness/security_regressions.yaml --output "$ROOT/.runs/security.jsonl"
clawops harness run --suite platform/configs/harness/policy_regressions.yaml --output "$ROOT/.runs/policy.jsonl"

echo "== Done =="
