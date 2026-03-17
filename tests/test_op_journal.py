"""Unit tests for the operation journal."""

from __future__ import annotations

import pathlib
import sqlite3

from pytest import MonkeyPatch

from clawops.op_journal import OperationJournal


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
    original = journal._ensure_schema

    def flaky_ensure_schema(conn: sqlite3.Connection) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        original(conn)

    monkeypatch.setattr(journal, "_ensure_schema", flaky_ensure_schema)

    journal.init()

    assert attempts["count"] == 2
