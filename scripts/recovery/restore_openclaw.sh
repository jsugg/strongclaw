#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCHIVE="${1:?backup archive required}"
DEST="${2:-$HOME/.openclaw-restore}"

mkdir -p "$DEST"
"$ROOT/scripts/recovery/backup_verify.sh" "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$DEST"
echo "Restored into $DEST"
