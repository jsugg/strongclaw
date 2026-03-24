# ACP Workers

ACP/acpx is the preferred structured worker plane for coding agents.

## Why ACP

- persistent sessions
- named workstreams
- machine-readable events
- no PTY scraping

## Included assets

- worker config templates
- coding/review system prompts
- worktree management commands
- reviewer/fixer workflow templates
- OpenClaw overlay for ACP agents

## Flow

1. create a worktree
2. run a coder ACP session
3. run tests in that worktree
4. run reviewer ACP session
5. merge only after reviewer approval
