#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$HOME/.openclaw/backups"
mkdir -p "$DEST"
ARCHIVE="$DEST/openclaw-$STAMP.tar.gz"
ARCHIVE_NAME="${ARCHIVE##*/}"

if warn_if_openclaw_missing "OpenClaw backup CLI unavailable; falling back to a tar archive."; then
  openclaw backup create "$ARCHIVE"
else
  tar -C "$HOME" --exclude=".openclaw/backups/$ARCHIVE_NAME" -czf "$ARCHIVE" ".openclaw"
fi
echo "$ARCHIVE"
