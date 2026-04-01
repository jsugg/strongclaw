# Repo workspace

Populate this directory with:

- `upstream/` — your OpenClaw fork or another target repo
- `worktrees/` — per-task git worktrees created by helper scripts

This repository intentionally does not vendor upstream source.
The `repo/upstream` + `repo/worktrees` contract is an operator/development
workflow and is not part of the baseline launch gate unless your rollout
policy explicitly includes repo/worktree automation.

Validate the layout before enabling ACP workers or path-sensitive overlays:

```bash
clawops repo doctor
```

Manage tracked worktrees through `clawops` instead of ad-hoc shell state:

```bash
clawops worktree list
clawops worktree new --branch feature/my-task
clawops worktree prune
```
