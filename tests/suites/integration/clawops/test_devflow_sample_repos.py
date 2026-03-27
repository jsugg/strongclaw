"""Integration coverage for multi-repo devflow qualification fixtures."""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from clawops.devflow import main
from clawops.workspace_bootstrap import resolve_bootstrap_profile
from tests.utils.helpers.devflow import (
    FIXTURE_REPOS_ROOT,
    init_git_repo,
    install_fake_devflow_backends,
)


@pytest.mark.parametrize(
    ("fixture_name", "expected_profile"),
    [
        ("python_basic", "python-basic"),
        ("node_basic", "node-basic"),
        ("go_basic", "go-basic"),
    ],
)
def test_devflow_qualifies_sample_repositories(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixture_name: str,
    expected_profile: str,
) -> None:
    repo_root = tmp_path / fixture_name
    shutil.copytree(FIXTURE_REPOS_ROOT / fixture_name, repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    assert resolve_bootstrap_profile(repo_root).profile_id == expected_profile

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--goal",
            f"qualify {fixture_name}",
            "--approved-by",
            "tester",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
