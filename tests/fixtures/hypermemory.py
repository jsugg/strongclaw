"""Pytest fixtures for hypermemory workspace builders."""

from __future__ import annotations

import pathlib

import pytest

from tests.utils.helpers.hypermemory import (
    FailingRerankProvider,
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    HypermemoryConfigWriter,
    HypermemoryWorkspaceFactory,
    StaticRerankProvider,
    build_rerank_workspace,
    build_workspace,
    write_hypermemory_config,
)
from tests.utils.helpers.mode import ServiceMode, resolve_service_mode
from tests.utils.helpers.qdrant_runtime import QdrantRuntime
from tests.utils.helpers.test_context import TestContext


@pytest.fixture
def hypermemory_workspace_factory(tmp_path: pathlib.Path) -> HypermemoryWorkspaceFactory:
    """Return a builder for baseline hypermemory workspaces."""

    def _factory() -> pathlib.Path:
        return build_workspace(tmp_path)

    return _factory


@pytest.fixture
def hypermemory_config_writer() -> HypermemoryConfigWriter:
    """Return the shared hypermemory config writer."""
    return write_hypermemory_config


@pytest.fixture
def rerank_workspace_factory(tmp_path: pathlib.Path) -> HypermemoryWorkspaceFactory:
    """Return a builder for rerank-focused hypermemory workspaces."""

    def _factory() -> pathlib.Path:
        return build_rerank_workspace(tmp_path)

    return _factory


@pytest.fixture
def qdrant_mode(request: pytest.FixtureRequest) -> ServiceMode:
    """Resolve Qdrant mock-or-real mode for the current test."""
    return resolve_service_mode(request, "qdrant")


@pytest.fixture
def qdrant_runtime(test_context: TestContext, qdrant_mode: ServiceMode) -> QdrantRuntime:
    """Return a managed Qdrant runtime bound to the current test context."""
    return QdrantRuntime(context=test_context, mode=qdrant_mode)


__all__ = [
    "FailingRerankProvider",
    "FakeEmbeddingProvider",
    "FakeQdrantBackend",
    "HypermemoryConfigWriter",
    "HypermemoryWorkspaceFactory",
    "QdrantRuntime",
    "ServiceMode",
    "StaticRerankProvider",
    "build_rerank_workspace",
    "build_workspace",
    "hypermemory_config_writer",
    "hypermemory_workspace_factory",
    "qdrant_mode",
    "qdrant_runtime",
    "rerank_workspace_factory",
    "write_hypermemory_config",
]
