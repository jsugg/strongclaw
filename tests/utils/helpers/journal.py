"""Reusable operation journal builders for tests."""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from clawops.op_journal import OperationJournal

type JournalFactory = Callable[[str], OperationJournal]


def create_journal(db_path: pathlib.Path) -> OperationJournal:
    """Create and initialize one operation journal."""
    journal = OperationJournal(db_path)
    journal.init()
    return journal
