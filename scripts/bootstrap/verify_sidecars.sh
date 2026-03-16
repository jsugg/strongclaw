#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

exec clawops verify-platform sidecars --repo-root "$ROOT" "$@"
