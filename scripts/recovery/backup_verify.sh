#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-latest}"
DIR="$HOME/.openclaw/backups"

if [[ "$TARGET" == "latest" ]]; then
  TARGET="$(ls -1t "$DIR"/*.tar.gz | head -n 1)"
fi

openclaw backup verify "$TARGET" || tar -tzf "$TARGET" >/dev/null
echo "Verified $TARGET"
