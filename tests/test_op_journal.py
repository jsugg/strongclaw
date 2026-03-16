"""Unit tests for the operation journal."""

from __future__ import annotations

import pathlib

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
