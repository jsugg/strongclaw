"""Unit tests for tracked-file git gates."""

from __future__ import annotations

import pathlib

from clawops.git_gates import capture_git_snapshot, check_tracked_mutations
from tests.utils.helpers.devflow import init_git_repo, write_strongclaw_shaped_repo


def test_git_gates_detect_tracked_file_mutations(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)

    before = capture_git_snapshot(repo_root)
    (repo_root / "README.md").write_text("# changed\n", encoding="utf-8")
    after = capture_git_snapshot(repo_root)
    result = check_tracked_mutations(before, after)

    assert result.ok is False
    assert "README.md" in result.mutated_paths
