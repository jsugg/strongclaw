"""Unit tests for the public devflow CLI surface."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.devflow import main
from clawops.devflow_state import begin_run
from tests.utils.helpers.devflow import init_git_repo, write_strongclaw_shaped_repo


def test_devflow_plan_is_deterministic(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)

    exit_code = main(["plan", "--project-root", str(repo_root), "--goal", "ship"])
    captured_first = json.loads(capsys.readouterr().out)
    assert exit_code == 0

    exit_code = main(["plan", "--project-root", str(repo_root), "--goal", "ship"])
    captured_second = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured_first == captured_second


def test_devflow_status_errors_on_unknown_run_id(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    exit_code = main(["status", "--project-root", str(repo_root), "--run-id", "missing"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert "unknown devflow run" in payload["message"]


def test_devflow_cancel_marks_non_terminal_runs(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    begin_run(
        repo_root / ".clawops" / "op_journal.sqlite",
        run_id="df_cancel",
        repo_root=repo_root,
        project_id="project-123",
        workspace_id="workspace-123",
        lane="default",
        goal="ship",
        run_profile="production",
        bootstrap_profile="strongclaw",
        workflow_path=repo_root / "workflow.yaml",
        plan_sha256="abc",
        requested_by="tester",
    )

    exit_code = main(["cancel", "--project-root", str(repo_root), "--run-id", "df_cancel"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["run"]["status"] == "cancelled"
