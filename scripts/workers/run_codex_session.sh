#!/usr/bin/env bash
set -euo pipefail
PROMPT="${1:?prompt required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/repo/upstream"
acpx codex "$PROMPT"
