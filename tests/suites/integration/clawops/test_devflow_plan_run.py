"""Integration coverage for devflow plan and run."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.devflow import main
from tests.utils.helpers.cli import PathPrepender
from tests.utils.helpers.devflow import (
    init_git_repo,
    install_fake_devflow_backends,
    write_strongclaw_shaped_repo,
)


def test_devflow_run_creates_run_state_and_manifest(
    tmp_path: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    prepend_path(bin_dir)

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--goal",
            "integration smoke",
            "--approved-by",
            "tester",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    run_root = repo_root / ".clawops" / "devflow" / payload["run_id"]
    run_json = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_root / "artifacts" / "manifest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["ok"] is True
    assert (run_root / "plan.json").exists()
    assert (run_root / "workflow.yaml").exists()
    assert (run_root / "artifacts" / "manifest.json").exists()
    assert (run_root / "summaries" / "developer.summary.json").exists()
    assert run_json["run"]["status"] == "succeeded"
    assert all(stage["status"] == "validated" for stage in manifest["stages"])


def test_devflow_run_fails_when_required_artifacts_are_missing(
    tmp_path: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir, create_expected_artifacts=False)
    prepend_path(bin_dir)

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--goal",
            "artifact regression",
            "--approved-by",
            "tester",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    run_root = repo_root / ".clawops" / "devflow" / payload["run_id"]
    manifest = json.loads((run_root / "artifacts" / "manifest.json").read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["stage"] == "architect"
    assert any("missing artifacts:" in message for message in payload["messages"])
    assert manifest["stages"][0]["status"] == "missing_artifacts"
