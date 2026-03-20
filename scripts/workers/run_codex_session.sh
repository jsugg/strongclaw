#!/usr/bin/env bash
set -euo pipefail
PROMPT="${1:?prompt required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE="$ROOT/repo/upstream"
BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"

exec "$(resolve_clawops_bin "$ROOT")" acp-runner \
  --backend codex \
  --session-type coder \
  --branch "$BRANCH" \
  --worktree "$WORKTREE" \
  --repo-root "$ROOT" \
  --prompt "$PROMPT"
