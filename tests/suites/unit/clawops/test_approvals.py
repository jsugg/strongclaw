"""Unit tests for the approvals CLI."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.approvals import main
from clawops.op_journal import OperationJournal
from tests.utils.helpers.journal import JournalFactory


def _seed_pending_operation(journal_factory: JournalFactory) -> tuple[OperationJournal, str]:
    journal = journal_factory()
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
    return journal, pending.op_id


def test_approvals_cli_queue_and_show(
    capsys: pytest.CaptureFixture[str],
    journal_factory: JournalFactory,
) -> None:
    journal, op_id = _seed_pending_operation(journal_factory)

    exit_code = main(["queue", "--db", str(journal.db_path)])
    captured = capsys.readouterr()
    queue = json.loads(captured.out)

    assert exit_code == 0
    assert [item["op_id"] for item in queue] == [op_id]

    exit_code = main(["show", "--db", str(journal.db_path), "--op-id", op_id])
    captured = capsys.readouterr()
    shown = json.loads(captured.out)

    assert exit_code == 0
    assert shown["op_id"] == op_id
    assert shown["review_status"] == "pending"


def test_approvals_cli_delegate_then_ingest_review(
    capsys: pytest.CaptureFixture[str],
    journal_factory: JournalFactory,
    tmp_path: pathlib.Path,
) -> None:
    journal, op_id = _seed_pending_operation(journal_factory)
    payload_file = tmp_path / "review.json"
    payload_file.write_text('{"review_id":"rvw-123"}', encoding="utf-8")

    exit_code = main(
        [
            "delegate",
            "--db",
            str(journal.db_path),
            "--op-id",
            op_id,
            "--reviewed-by",
            "operator",
            "--to",
            "reviewer-acp-claude",
            "--note",
            "delegate to ACP",
        ]
    )
    capsys.readouterr()
    delegated = journal.get(op_id)

    assert exit_code == 0
    assert delegated.review_status == "delegated"
    assert delegated.review_target == "reviewer-acp-claude"

    exit_code = main(
        [
            "ingest-review",
            "--db",
            str(journal.db_path),
            "--op-id",
            op_id,
            "--reviewed-by",
            "reviewer-acp-claude",
            "--decision",
            "allow",
            "--note",
            "approved",
            "--payload-file",
            str(payload_file),
        ]
    )
    capsys.readouterr()
    approved = journal.get(op_id)

    assert exit_code == 0
    assert approved.status == "approved"
    assert approved.approved_by == "reviewer-acp-claude"
    assert approved.review_payload_json is not None
