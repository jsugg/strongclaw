#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/openclaw.sh
source "$ROOT/scripts/lib/openclaw.sh"

TARGET="${1:-latest}"
DIR="$HOME/.openclaw/backups"

if [[ "$TARGET" == "latest" ]]; then
  TARGET="$(ls -1t "$DIR"/*.tar.gz | head -n 1)"
fi

if warn_if_openclaw_missing "OpenClaw backup verification unavailable; falling back to tar verification."; then
  openclaw backup verify "$TARGET"
else
  tar -tzf "$TARGET" >/dev/null
fi
echo "Verified $TARGET"
