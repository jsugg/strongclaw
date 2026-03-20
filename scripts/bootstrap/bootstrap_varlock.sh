#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"

ensure_varlock_installed "$VARLOCK_VERSION"
echo "Varlock ${VARLOCK_VERSION} is installed."
