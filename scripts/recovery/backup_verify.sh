#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

TARGET="${1:-latest}"
DIR="$HOME/.openclaw/backups"

if [[ "$TARGET" == "latest" ]]; then
  shopt -s nullglob
  archives=("$DIR"/*.tar.gz)
  shopt -u nullglob
  if [[ "${#archives[@]}" -eq 0 ]]; then
    echo "ERROR: no backup archives found in $DIR" >&2
    exit 1
  fi

  TARGET="${archives[0]}"
  for archive in "${archives[@]}"; do
    if [[ "$archive" -nt "$TARGET" ]]; then
      TARGET="$archive"
    fi
  done
fi

if warn_if_openclaw_missing "OpenClaw backup verification unavailable; falling back to tar verification."; then
  openclaw backup verify "$TARGET"
else
  tar -tzf "$TARGET" >/dev/null
fi
echo "Verified $TARGET"
