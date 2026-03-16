#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
clawops context index --config "$ROOT/platform/configs/context/context-service.yaml" --repo "$ROOT"
