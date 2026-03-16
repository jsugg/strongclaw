#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:?worktree path required}"
git worktree remove "$TARGET" --force
