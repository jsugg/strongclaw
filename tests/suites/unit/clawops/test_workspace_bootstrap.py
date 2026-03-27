"""Unit tests for devflow bootstrap profile resolution."""

from __future__ import annotations

import pathlib

from clawops.workspace_bootstrap import resolve_bootstrap_profile
from tests.utils.helpers.devflow import write_strongclaw_shaped_repo


def test_strongclaw_profile_wins_over_generic_python(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)

    profile = resolve_bootstrap_profile(repo_root)

    assert profile.profile_id == "strongclaw"
    assert "lint" in profile.commands
    assert profile.commands["install"][0] == ("uv", "sync", "--locked")


def test_generic_python_profile_is_deterministic(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "sample-python"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    first = resolve_bootstrap_profile(repo_root)
    second = resolve_bootstrap_profile(repo_root)

    assert first.profile_id == "python-basic"
    assert first.to_dict() == second.to_dict()
