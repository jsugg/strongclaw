#!/usr/bin/env bash
set -euo pipefail

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$HOME/.openclaw/backups"
mkdir -p "$DEST"
ARCHIVE="$DEST/openclaw-$STAMP.tar.gz"

openclaw backup create "$ARCHIVE" || tar -czf "$ARCHIVE" "$HOME/.openclaw"
echo "$ARCHIVE"
