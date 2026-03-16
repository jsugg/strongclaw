#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:?branch required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE="$ROOT/repo/worktrees/$BRANCH"

cd "$WORKTREE"
echo "== coder pass =="
acpx codex "Review the current branch, fix failing tests, and summarize the patch."

echo "== reviewer pass =="
acpx claude "Review the current branch diff, tests, and rollback risk. Return approve/reject/needs changes."
