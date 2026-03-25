"""Pytest fixtures for hypermemory workspace builders."""

from __future__ import annotations

import pathlib

import pytest

from tests.utils.helpers.hypermemory import (
    HypermemoryConfigWriter,
    HypermemoryWorkspaceFactory,
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
