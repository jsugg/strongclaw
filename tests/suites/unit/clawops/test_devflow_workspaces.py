"""Unit tests for devflow workspace planning."""

from __future__ import annotations

import pathlib

from clawops.devflow_workspaces import DevflowWorkspacePlanner
from tests.utils.helpers.devflow import init_git_repo, write_strongclaw_shaped_repo


def test_verify_only_workspace_does_not_reuse_mutable_primary(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)

    planner = DevflowWorkspacePlanner(
        repo_root=repo_root, run_root=repo_root / ".clawops" / "devflow" / "run"
    )
    primary = planner.prepare(
        stage_name="developer",
        workspace_mode="mutable_primary",
        source_root=repo_root,
    )
    (primary.root / "README.md").write_text("# changed\n", encoding="utf-8")

    reviewer = planner.prepare(
        stage_name="reviewer",
        workspace_mode="verify_only",
        source_root=primary.root,
    )

    assert primary.root != reviewer.root
    assert reviewer.descriptor.kind == "git_worktree"
    assert (reviewer.root / "README.md").read_text(encoding="utf-8") == "# changed\n"
