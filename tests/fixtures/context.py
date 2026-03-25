"""Pytest fixtures for context workspace builders."""

from __future__ import annotations

import pathlib

import pytest

from tests.utils.helpers.context import (
    ContextPayload,
    ContextProjectFactory,
    ContextRepoFactory,
    build_context_project,
    build_context_repo,
    write_context_config,
)


@pytest.fixture
def context_repo_factory(tmp_path: pathlib.Path) -> ContextRepoFactory:
    """Return a builder for isolated context repos."""

    def _factory(
        *,
        files: dict[str, str] | None = None,
        repo_name: str = "repo",
        config_name: str = "context.yaml",
        config_payload: ContextPayload | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        return build_context_repo(
            tmp_path,
            files=files,
            repo_name=repo_name,
            config_name=config_name,
            config_payload=config_payload,
        )

    return _factory


@pytest.fixture
def context_project_factory(tmp_path: pathlib.Path) -> ContextProjectFactory:
    """Return a builder for isolated context-enabled projects."""

    def _factory(
        *,
        project_name: str = "project",
        workspace_name: str = "workspace",
        config_name: str = "context.yaml",
        config_payload: ContextPayload | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        return build_context_project(
            tmp_path,
            project_name=project_name,
            workspace_name=workspace_name,
            config_name=config_name,
            config_payload=config_payload,
        )

    return _factory


__all__ = [
    "ContextPayload",
    "ContextProjectFactory",
    "ContextRepoFactory",
    "build_context_project",
    "build_context_repo",
    "context_project_factory",
    "context_repo_factory",
    "write_context_config",
]
