"""Integration coverage for devflow recovery and audit paths."""

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


def test_devflow_recovery_audit_and_resume(
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

    exit_code = main(["run", "--repo-root", str(repo_root), "--goal", "recovery smoke"])
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

    exit_code = main(["audit", "--repo-root", str(repo_root), "--run-id", payload["run_id"]])
    audit_payload = json.loads(capsys.readouterr().out)
    bundle = json.loads(pathlib.Path(audit_payload["bundle_path"]).read_text(encoding="utf-8"))

    assert exit_code == 0
    assert bundle["artifact_manifest"] is not None
    assert any(event["event_kind"] == "run_resumed" for event in bundle["events"])
    assert any(
        event["stage_name"] == "lead" and event["event_kind"] == "stage_retried"
        for event in bundle["events"]
    )
