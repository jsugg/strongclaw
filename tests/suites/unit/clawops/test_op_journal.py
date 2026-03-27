"""Unit tests for the operation journal."""

from __future__ import annotations

import pathlib
import sqlite3
from typing import Any, Callable, cast

from pytest import MonkeyPatch

from clawops.op_journal import LeaseConflictError, OperationJournal


def test_begin_is_idempotent(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op1 = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    op2 = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    assert op1.op_id == op2.op_id


def test_approve_sets_metadata_and_validates_transition(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    pending = journal.transition(
        op.op_id,
        "pending_approval",
        policy_decision="require_approval",
        approval_required=True,
    )
    assert pending.status == "pending_approval"
    approved = journal.approve(op.op_id, approved_by="operator", note="looks good")
    assert approved.status == "approved"
    assert approved.approved_by == "operator"
    assert approved.approved_at_ms is not None
    assert approved.review_status == "approved"
    assert approved.reviewed_by == "operator"
    assert approved.review_note == "looks good"


def test_approve_allows_manual_proposed_operations_without_approval_gate(
    tmp_path: pathlib.Path,
) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )

    approved = journal.approve(op.op_id, approved_by="operator", note="manual staging")

    assert approved.status == "approved"
    assert approved.approved_by == "operator"


def test_approve_requires_pending_state_for_approval_required_operations(
    tmp_path: pathlib.Path,
) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    journal.transition(
        op.op_id,
        "proposed",
        policy_decision="require_approval",
        approval_required=True,
    )

    try:
        journal.approve(op.op_id, approved_by="operator")
    except ValueError as exc:
        assert "approval-required operation must be pending_approval" in str(exc)
    else:
        raise AssertionError("expected approval-required proposed operation to fail approval")


def test_invalid_transition_is_rejected(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    journal.transition(op.op_id, "failed", error="policy denied")
    try:
        journal.transition(op.op_id, "running")
    except ValueError as exc:
        assert "invalid operation transition" in str(exc)
    else:
        raise AssertionError("expected invalid transition to fail")


def test_transition_persists_result_metadata(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    approved = journal.transition(op.op_id, "approved")
    running = journal.transition(approved.op_id, "running")
    completed = journal.transition(
        running.op_id,
        "succeeded",
        result_ok=True,
        result_status_code=200,
        result_body_excerpt="ok",
    )
    assert completed.result_ok == 1
    assert completed.result_status_code == 200
    assert completed.result_body_excerpt == "ok"


def test_queue_delegate_and_ingest_review_preserve_review_metadata(
    tmp_path: pathlib.Path,
) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="github:repo",
        kind="github_pull_merge",
        trust_zone="reviewer",
        normalized_target="github://example/repo/pulls/123/merge",
        inputs={"merge_method": "squash"},
    )
    pending = journal.transition(
        op.op_id,
        "pending_approval",
        policy_decision="require_approval",
        approval_required=True,
        review_mode="delegate_recommend",
        review_target="reviewer-acp-claude",
        review_status="pending",
        review_payload_json='{"delegate_to":"reviewer-acp-claude"}',
    )

    queued = journal.queue()
    assert [item.op_id for item in queued] == [pending.op_id]

    delegated = journal.delegate(
        pending.op_id,
        reviewed_by="operator",
        delegate_to="reviewer-acp-claude",
        note="needs ACP review",
    )
    assert delegated.status == "pending_approval"
    assert delegated.review_status == "delegated"
    assert delegated.review_target == "reviewer-acp-claude"
    assert delegated.reviewed_by == "operator"

    approved = journal.ingest_review(
        pending.op_id,
        reviewed_by="reviewer-acp-claude",
        decision="allow",
        note="approved by ACP reviewer",
    )
    assert approved.status == "approved"
    assert approved.approved_by == "reviewer-acp-claude"
    assert approved.review_status == "approved"
    assert approved.review_payload_json is not None


def test_reject_moves_pending_operation_to_terminal_review_state(
    tmp_path: pathlib.Path,
) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="github:repo",
        kind="github_comment",
        trust_zone="automation",
        normalized_target="github://example/repo/issues/123",
        inputs={"body": "hello"},
    )
    pending = journal.transition(
        op.op_id,
        "pending_approval",
        policy_decision="require_approval",
        approval_required=True,
        review_mode="manual",
        review_status="pending",
    )

    rejected = journal.reject(pending.op_id, reviewed_by="operator", note="denied")

    assert rejected.status == "rejected"
    assert rejected.review_status == "rejected"
    assert rejected.reviewed_by == "operator"
    assert rejected.last_error == "denied"


def test_transition_persists_execution_contract_metadata(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    op = journal.begin(
        scope="telegram:owner",
        kind="webhook_post",
        trust_zone="automation",
        normalized_target="https://example.invalid",
        inputs={"body": "hello"},
    )
    updated = journal.transition(
        op.op_id,
        "approved",
        policy_decision="allow",
        policy_decision_json='{"decision":"allow","matched_rules":[],"reasons":[]}',
        execution_contract_version=1,
        execution_contract_json='{"inputs_hash":"hash","kind":"webhook_post","normalized_target":"https://example.invalid","policy_decision":"allow","scope":"telegram:owner","trust_zone":"automation","version":1}',
    )

    assert updated.execution_contract_version == 1
    assert updated.execution_contract_json is not None


def test_connect_retries_transient_open_error(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    attempts = {"count": 0}
    original = cast(
        Callable[[sqlite3.Connection], None],
        cast(Any, journal)._ensure_schema,
    )

    def flaky_ensure_schema(conn: sqlite3.Connection) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        original(conn)

    monkeypatch.setattr(journal, "_ensure_schema", flaky_ensure_schema)

    journal.init()

    assert attempts["count"] == 2


def test_session_leases_are_visible_and_releasable(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()

    lease = journal.acquire_lease(
        lock_identity="project:workspace:lane:developer:implement",
        session_identity="codex:project:workspace:lane:developer",
        backend="codex",
        project_id="project",
        workspace_id="workspace",
        lane="lane",
        role="developer",
        operation_kind="implement",
        holder=str(tmp_path),
        ttl_seconds=60,
        metadata={"foo": "bar"},
    )

    active = journal.list_leases(active_only=True)
    assert [item.lease_id for item in active] == [lease.lease_id]

    released = journal.release_lease(lease.lease_id, released_by="tester")
    assert released.status == "released"
    assert journal.list_leases(active_only=True) == []


def test_session_lease_conflicts_raise(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()
    journal.acquire_lease(
        lock_identity="project:workspace:lane:developer:implement",
        session_identity="codex:project:workspace:lane:developer",
        backend="codex",
        project_id="project",
        workspace_id="workspace",
        lane="lane",
        role="developer",
        operation_kind="implement",
        holder=str(tmp_path),
        ttl_seconds=60,
    )

    try:
        journal.acquire_lease(
            lock_identity="project:workspace:lane:developer:implement",
            session_identity="claude:project:workspace:lane:developer",
            backend="claude",
            project_id="project",
            workspace_id="workspace",
            lane="lane",
            role="developer",
            operation_kind="implement",
            holder=str(tmp_path),
            ttl_seconds=60,
        )
    except LeaseConflictError as exc:
        assert "active lease already exists" in str(exc)
    else:
        raise AssertionError("expected duplicate active lease to fail")


def test_devflow_tables_are_created_and_stuck_runs_are_queryable(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "journal.sqlite"
    journal = OperationJournal(db)
    journal.init()

    with journal.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.execute(
            """
            INSERT INTO devflow_run (
              run_id,
              created_at_ms,
              updated_at_ms,
              status,
              repo_root,
              project_id,
              workspace_id,
              lane,
              goal,
              run_profile,
              bootstrap_profile,
              workflow_path,
              plan_sha256,
              current_stage_name,
              requested_by,
              resume_token,
              summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "df_stale",
                1,
                1,
                "running",
                "/repo",
                "project-1",
                "workspace-1",
                "default",
                "goal",
                "production",
                "strongclaw",
                "/repo/workflow.yaml",
                "hash",
                "developer",
                "tester",
                None,
                None,
            ),
        )

    assert {"devflow_run", "devflow_stage", "devflow_stage_event"} <= tables
    stuck = journal.list_stuck_devflow_runs(older_than_ms=0)
    assert [item["run_id"] for item in stuck] == ["df_stale"]
