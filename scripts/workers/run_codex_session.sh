#!/usr/bin/env bash
set -euo pipefail
PROMPT="${1:?prompt required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE="$ROOT/repo/upstream"
BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)"

exec clawops acp-runner \
  --backend codex \
  --session-type coder \
  --branch "$BRANCH" \
  --worktree "$WORKTREE" \
  --repo-root "$ROOT" \
  --prompt "$PROMPT"
