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


def test_synced_workspace_skips_regenerable_directories(tmp_path: pathlib.Path) -> None:
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
    (primary.root / ".venv" / "bin").mkdir(parents=True)
    (primary.root / ".venv" / "bin" / "python").write_text("shim", encoding="utf-8")
    (primary.root / "node_modules" / "left-pad").mkdir(parents=True)
    (primary.root / "node_modules" / "left-pad" / "index.js").write_text(
        "module.exports = 0;\n",
        encoding="utf-8",
    )
    (primary.root / "dist").mkdir()
    (primary.root / "dist" / "artifact.txt").write_text("compiled\n", encoding="utf-8")

    reviewer = planner.prepare(
        stage_name="reviewer",
        workspace_mode="verify_only",
        source_root=primary.root,
    )

    assert (reviewer.root / "README.md").exists()
    assert not (reviewer.root / ".venv").exists()
    assert not (reviewer.root / "node_modules").exists()
    assert not (reviewer.root / "dist").exists()
