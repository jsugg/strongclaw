"""Unit tests for approval reviewer packet dispatch."""

from __future__ import annotations

import json
import os
import pathlib

from clawops.approval_dispatch import dispatch_pending_approval
from clawops.op_journal import Operation, OperationJournal
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.journal import create_journal


def _seed_pending_operation(journal: OperationJournal, *, root: pathlib.Path) -> Operation:
    op = journal.begin(
        scope="session-1",
        kind="workflow-dispatch",
        trust_zone="automation",
        normalized_target=root.as_posix(),
        inputs={"task": "ship"},
    )
    return journal.transition(
        op.op_id,
        "pending_approval",
        policy_decision="require_approval",
        policy_decision_json=json.dumps(
            {"decision": "require_approval", "reason": "manual review"}
        ),
        execution_contract_version=1,
        execution_contract_json=json.dumps({"version": 1, "kind": "workflow-dispatch"}),
        approval_required=True,
        review_mode="manual",
        review_status="pending",
        review_payload_json=json.dumps({"checklist": ["confirm release notes"]}),
    )


def test_dispatch_pending_approval_writes_packet_and_updates_journal(
    tmp_path: pathlib.Path,
) -> None:
    journal = create_journal(tmp_path / "workflow.sqlite")
    pending = _seed_pending_operation(journal, root=tmp_path)

    outcome = dispatch_pending_approval(journal=journal, operation=pending)

    assert outcome.dispatched is True
    assert outcome.error is None
    assert outcome.artifact_path.exists()
    packet = json.loads(outcome.artifact_path.read_text(encoding="utf-8"))
    assert packet["opId"] == pending.op_id
    assert packet["review"]["status"] == "pending"
    assert packet["policy"]["decision"] == "require_approval"
    if os.name != "nt":
        assert (outcome.artifact_path.parent.stat().st_mode & 0o777) == 0o700
        assert (outcome.artifact_path.stat().st_mode & 0o777) == 0o600
    persisted = journal.get(pending.op_id)
    assert persisted.review_artifact_path == outcome.artifact_path.as_posix()
    assert persisted.status == "pending_approval"


def test_dispatch_pending_approval_surfaces_local_write_failures(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    journal = create_journal(tmp_path / "workflow.sqlite")
    pending = _seed_pending_operation(journal, root=tmp_path)

    def _raise_write_failure(_path: pathlib.Path, _value: object, *, indent: int = 2) -> None:
        del indent
        raise OSError("disk full")

    import clawops.approval_dispatch as approval_dispatch

    test_context.patch.patch_object(
        approval_dispatch,
        "write_json",
        new=_raise_write_failure,
    )

    outcome = dispatch_pending_approval(journal=journal, operation=pending)

    assert outcome.dispatched is False
    assert outcome.error is not None
    assert "disk full" in outcome.error
    assert journal.get(pending.op_id).review_artifact_path is None
