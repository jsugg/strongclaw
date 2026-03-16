#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="$ROOT/repo/upstream"
BRANCH="${1:?branch name required}"
TARGET="$ROOT/repo/worktrees/$BRANCH"

mkdir -p "$ROOT/repo/worktrees"
git -C "$REPO" fetch --all
git -C "$REPO" worktree add -B "$BRANCH" "$TARGET" HEAD
echo "$TARGET"
