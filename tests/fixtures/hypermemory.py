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


__all__ = [
    "FailingRerankProvider",
    "FakeEmbeddingProvider",
    "FakeQdrantBackend",
    "HypermemoryConfigWriter",
    "HypermemoryWorkspaceFactory",
    "StaticRerankProvider",
    "build_rerank_workspace",
    "build_workspace",
    "hypermemory_config_writer",
    "hypermemory_workspace_factory",
    "rerank_workspace_factory",
    "write_hypermemory_config",
]
