"""Unit tests for devflow state persistence."""

from __future__ import annotations

import pathlib

from clawops.devflow_state import (
    begin_run,
    cancel_run,
    get_run,
    mark_run_succeeded,
    record_stage_completed,
    record_stage_failed,
    record_stage_started,
    resume_run,
)


def test_devflow_state_creates_and_completes_run(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    db_path = tmp_path / "journal.sqlite"
    run = begin_run(
        db_path,
        run_id="df_test",
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

    assert run.status == "planned"

    stage = record_stage_started(
        db_path,
        run_id="df_test",
        stage_name="developer",
        stage_index=1,
        role="developer",
        workspace_root=repo_root,
    )
    assert stage.status == "running"

    record_stage_completed(
        db_path,
        run_id="df_test",
        stage_name="developer",
    )
    run = mark_run_succeeded(db_path, run_id="df_test", summary={"ok": True})
    view = get_run(db_path, run_id="df_test")

    assert run.status == "succeeded"
    assert view.stages[0].status == "succeeded"


def test_devflow_state_resume_and_cancel_behaviors(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    db_path = tmp_path / "journal.sqlite"
    begin_run(
        db_path,
        run_id="df_test_resume",
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
    record_stage_started(
        db_path,
        run_id="df_test_resume",
        stage_name="developer",
        stage_index=1,
        role="developer",
        workspace_root=repo_root,
    )
    record_stage_failed(
        db_path,
        run_id="df_test_resume",
        stage_name="developer",
        reason="boom",
    )

    resumed = resume_run(db_path, run_id="df_test_resume")

    assert resumed.run.status == "running"
    assert resumed.next_incomplete_stage() is not None
    restarted_stage = record_stage_started(
        db_path,
        run_id="df_test_resume",
        stage_name="developer",
        stage_index=1,
        role="developer",
        workspace_root=repo_root,
    )
    resumed = get_run(db_path, run_id="df_test_resume")

    assert restarted_stage.retry_count == 1
    assert any(
        event.stage_name == "developer" and event.event_kind == "stage_retried"
        for event in resumed.events
    )

    cancelled = cancel_run(db_path, run_id="df_test_resume", requested_by="tester")

    assert cancelled.status == "cancelled"
