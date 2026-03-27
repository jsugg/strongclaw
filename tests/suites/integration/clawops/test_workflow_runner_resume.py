"""Integration coverage for devflow resume semantics."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from clawops.devflow import main
from tests.utils.helpers.devflow import (
    init_git_repo,
    install_fake_devflow_backends,
    write_strongclaw_shaped_repo,
)


def test_devflow_resume_skips_completed_stages_and_finishes_next_incomplete_stage(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    exit_code = main(["run", "--repo-root", str(repo_root), "--goal", "resume smoke"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["stage"] == "lead"

    exit_code = main(
        [
            "resume",
            "--repo-root",
            str(repo_root),
            "--run-id",
            payload["run_id"],
            "--approved-by",
            "tester",
        ]
    )
    resumed = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert resumed["ok"] is True
