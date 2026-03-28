"""Integration coverage for devflow recovery and audit paths."""

from __future__ import annotations

import errno
import json
import pathlib

import pytest

from clawops.devflow import main
from clawops.devflow_roles import WorkspaceMode
from clawops.devflow_workspaces import DevflowWorkspacePlanner, PlannedWorkspace
from tests.plugins.infrastructure.context import TestContext
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

    exit_code = main(["run", "--project-root", str(repo_root), "--goal", "recovery smoke"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["stage"] == "lead"

    exit_code = main(
        [
            "resume",
            "--project-root",
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

    exit_code = main(["audit", "--project-root", str(repo_root), "--run-id", payload["run_id"]])
    audit_payload = json.loads(capsys.readouterr().out)
    bundle = json.loads(pathlib.Path(audit_payload["bundle_path"]).read_text(encoding="utf-8"))

    assert exit_code == 0
    assert bundle["artifact_manifest"] is not None
    assert any(event["event_kind"] == "run_resumed" for event in bundle["events"])
    assert any(
        event["stage_name"] == "lead" and event["event_kind"] == "stage_retried"
        for event in bundle["events"]
    )


def test_devflow_workspace_failure_marks_run_failed_and_audit_still_works(
    tmp_path: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    prepend_path(bin_dir)

    original_prepare = DevflowWorkspacePlanner.prepare
    failure_budget = {"reviewer": 1}

    def _flaky_prepare(
        self: DevflowWorkspacePlanner,
        *,
        stage_name: str,
        workspace_mode: WorkspaceMode,
        source_root: pathlib.Path,
    ) -> PlannedWorkspace:
        if stage_name == "reviewer" and failure_budget["reviewer"] > 0:
            failure_budget["reviewer"] -= 1
            raise OSError(errno.ENOSPC, "no space left on device")
        return original_prepare(
            self,
            stage_name=stage_name,
            workspace_mode=workspace_mode,
            source_root=source_root,
        )

    test_context.patch.patch_object(DevflowWorkspacePlanner, "prepare", new=_flaky_prepare)

    exit_code = main(["run", "--project-root", str(repo_root), "--goal", "workspace recovery"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["stage"] == "reviewer"

    status_exit = main(["status", "--project-root", str(repo_root), "--run-id", payload["run_id"]])
    status_payload = json.loads(capsys.readouterr().out)

    assert status_exit == 0
    assert status_payload["run"]["status"] == "failed"

    audit_exit = main(["audit", "--project-root", str(repo_root), "--run-id", payload["run_id"]])
    audit_payload = json.loads(capsys.readouterr().out)
    bundle = json.loads(pathlib.Path(audit_payload["bundle_path"]).read_text(encoding="utf-8"))

    assert audit_exit == 0
    assert any(
        event["stage_name"] == "reviewer" and event["event_kind"] == "stage_failed"
        for event in bundle["events"]
    )

    resume_exit = main(
        [
            "resume",
            "--project-root",
            str(repo_root),
            "--run-id",
            payload["run_id"],
            "--approved-by",
            "tester",
        ]
    )
    resumed = json.loads(capsys.readouterr().out)

    assert resume_exit == 0
    assert resumed["ok"] is True
