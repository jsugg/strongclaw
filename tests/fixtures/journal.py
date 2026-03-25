"""Pytest fixtures for operation journal builders."""

from __future__ import annotations

import pathlib

import pytest

from clawops.op_journal import OperationJournal
from tests.utils.helpers.journal import JournalFactory, create_journal


@pytest.fixture
def journal_factory(tmp_path: pathlib.Path) -> JournalFactory:
    """Return a factory for isolated operation journals."""

    def _factory(name: str = "journal.sqlite") -> OperationJournal:
        return create_journal(tmp_path / name)

    return _factory
