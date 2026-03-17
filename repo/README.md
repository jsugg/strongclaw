# Repo workspace

Populate this directory with:

- `upstream/` — your OpenClaw fork or another target repo
- `worktrees/` — per-task git worktrees created by helper scripts

This repository intentionally does not vendor upstream source.

Validate the layout before enabling ACP workers or path-sensitive overlays:

```bash
clawops repo --repo-root "$(pwd)" doctor
```

Manage tracked worktrees through `clawops` instead of ad-hoc shell state:

```bash
clawops worktree --repo-root "$(pwd)" list
clawops worktree --repo-root "$(pwd)" new --branch feature/my-task
clawops worktree --repo-root "$(pwd)" prune
```
