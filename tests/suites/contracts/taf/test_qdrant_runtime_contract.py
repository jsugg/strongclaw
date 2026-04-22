"""Contracts for managed Qdrant runtime helpers."""

from __future__ import annotations

from tests.utils.helpers.hypermemory import FakeQdrantBackend
from tests.utils.helpers.qdrant_runtime import QdrantRuntime
from tests.utils.helpers.test_context import TestContext


def test_qdrant_runtime_uses_fake_in_mock_mode() -> None:
    runtime = QdrantRuntime(context=TestContext(), mode="mock")
    assert isinstance(runtime.connect(), FakeQdrantBackend)


def test_qdrant_runtime_collection_name_contains_resource_prefix() -> None:
    ctx = TestContext()
    runtime = QdrantRuntime(context=ctx, mode="mock")

    assert ctx.resource_prefix in runtime.collection_name
