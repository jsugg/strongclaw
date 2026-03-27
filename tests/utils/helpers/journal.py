"""Reusable operation journal builders for tests."""

from __future__ import annotations

import pathlib
from typing import Protocol

from clawops.op_journal import OperationJournal


class JournalFactory(Protocol):
    """Callable journal factory used by tests."""

    def __call__(self, name: str = "journal.sqlite") -> OperationJournal: ...


def create_journal(db_path: pathlib.Path) -> OperationJournal:
    """Create and initialize one operation journal."""
    journal = OperationJournal(db_path)
    journal.init()
    return journal
