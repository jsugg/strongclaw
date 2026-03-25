"""Reusable context workspace builders for tests."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Mapping

from clawops.common import write_yaml

type ContextPayload = Mapping[str, object]
type ContextRepoFactory = Callable[..., tuple[pathlib.Path, pathlib.Path]]
type ContextProjectFactory = Callable[..., tuple[pathlib.Path, pathlib.Path, pathlib.Path]]

_DEFAULT_CONTEXT_CONFIG: ContextPayload = {"index": {"db_path": ".clawops/context.sqlite"}}
_DEFAULT_REPO_FILES = {
    "auth.py": "def validate_jwt():\n    return True\n",
}


def write_context_config(
    config_path: pathlib.Path,
    payload: ContextPayload | None = None,
) -> pathlib.Path:
    """Write a context-service config file and return its path."""
    write_yaml(config_path, _DEFAULT_CONTEXT_CONFIG if payload is None else payload)
    return config_path


def build_context_repo(
    tmp_path: pathlib.Path,
    *,
    files: Mapping[str, str] | None = None,
    repo_name: str = "repo",
    config_name: str = "context.yaml",
    config_payload: ContextPayload | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a repo plus context config for context-service tests."""
    repo = tmp_path / repo_name
    repo.mkdir()
    for rel_path, content in (_DEFAULT_REPO_FILES if files is None else files).items():
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    config_path = tmp_path / config_name
    write_context_config(config_path, config_payload)
    return repo, config_path


def build_context_project(
    tmp_path: pathlib.Path,
    *,
    project_name: str = "project",
    workspace_name: str = "workspace",
    config_name: str = "context.yaml",
    config_payload: ContextPayload | None = None,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Create a project/workspace pair plus a context config under the project."""
    project_root = tmp_path / project_name
    workspace = project_root / workspace_name
    project_root.mkdir()
    workspace.mkdir()
    config_path = project_root / config_name
    write_context_config(config_path, config_payload)
    return project_root, workspace, config_path
