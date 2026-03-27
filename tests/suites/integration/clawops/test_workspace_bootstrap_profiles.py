"""Integration tests for bootstrap profile resolution on fixture repositories."""

from __future__ import annotations

import pathlib

from clawops.workspace_bootstrap import resolve_bootstrap_profile
from tests.utils.helpers.devflow import copy_fixture_repo


def test_bootstrap_profiles_resolve_for_fixture_repositories(tmp_path: pathlib.Path) -> None:
    python_repo = copy_fixture_repo("python_basic", tmp_path / "python_basic")
    node_repo = copy_fixture_repo("node_basic", tmp_path / "node_basic")
    go_repo = copy_fixture_repo("go_basic", tmp_path / "go_basic")

    assert resolve_bootstrap_profile(python_repo).profile_id == "python-basic"
    assert resolve_bootstrap_profile(node_repo).profile_id == "node-basic"
    assert resolve_bootstrap_profile(go_repo).profile_id == "go-basic"
