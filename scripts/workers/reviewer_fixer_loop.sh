#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:?branch required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE="$ROOT/repo/worktrees/$BRANCH"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

echo "== coder pass =="
run_clawops "$ROOT" acp-runner \
  --backend codex \
  --session-type coder \
  --branch "$BRANCH" \
  --worktree "$WORKTREE" \
  --repo-root "$ROOT" \
  --prompt "Review the current branch, fix failing tests, and summarize the patch."

echo "== reviewer pass =="
run_clawops "$ROOT" acp-runner \
  --backend claude \
  --session-type reviewer \
  --branch "$BRANCH" \
  --worktree "$WORKTREE" \
  --repo-root "$ROOT" \
  --prompt "Review the current branch diff, tests, and rollback risk. Return approve/reject/needs changes."
