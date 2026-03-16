#!/usr/bin/env bash
set -euo pipefail

WORKFLOW="${1:?workflow required}"
shift || true
clawops workflow --workflow "$WORKFLOW" "$@"
