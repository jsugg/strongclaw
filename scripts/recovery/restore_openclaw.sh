#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?backup archive required}"
DEST="${2:-$HOME/.openclaw-restore}"

mkdir -p "$DEST"
./scripts/recovery/backup_verify.sh "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$DEST"
echo "Restored into $DEST"
