"""Pytest fixtures for observability tests."""

from __future__ import annotations

import pytest

from clawops import observability as clawops_observability
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.observability import RecordingExporter, configure_test_tracing


@pytest.fixture
def tracing_exporter(test_context: TestContext) -> RecordingExporter:
    """Install an in-memory exporter for tests under the observability boundary."""
    return configure_test_tracing(test_context, clawops_observability)


__all__ = [
    "tracing_exporter",
]
