"""Pytest fixtures for observability tests."""

from __future__ import annotations

import pytest

from clawops import observability as clawops_observability
from tests.utils.helpers.observability import RecordingExporter, configure_test_tracing


@pytest.fixture
def tracing_exporter(monkeypatch: pytest.MonkeyPatch) -> RecordingExporter:
    """Install an in-memory exporter for tests under the observability boundary."""
    return configure_test_tracing(monkeypatch, clawops_observability)


__all__ = [
    "RecordingExporter",
    "configure_test_tracing",
    "tracing_exporter",
]
