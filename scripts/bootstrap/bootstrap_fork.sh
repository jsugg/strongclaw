#!/usr/bin/env bash
set -euo pipefail

DEST="${1:-$HOME/Projects/openclaw-platform-bootstrap/repo/upstream}"
UPSTREAM_URL="${2:-https://github.com/openclaw/openclaw.git}"
FORK_URL="${3:-}"

mkdir -p "$(dirname "$DEST")"
if [[ ! -d "$DEST/.git" ]]; then
  git clone "$UPSTREAM_URL" "$DEST"
fi

git -C "$DEST" remote remove upstream >/dev/null 2>&1 || true
git -C "$DEST" remote add upstream "$UPSTREAM_URL"

if [[ -n "$FORK_URL" ]]; then
  git -C "$DEST" remote remove origin >/dev/null 2>&1 || true
  git -C "$DEST" remote add origin "$FORK_URL"
fi

git -C "$DEST" fetch --all --tags
echo "Repo prepared at $DEST"
