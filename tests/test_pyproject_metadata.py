"""Tests for project dependency metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _project_dependencies() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    payload = tomllib.loads(root.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    assert isinstance(dependencies, list)
    return [str(item) for item in dependencies]


def test_rerank_dependency_markers_cover_supported_host_matrix() -> None:
    dependencies = _project_dependencies()
    sentence_transformers_dep = next(
        dependency
        for dependency in dependencies
        if dependency.startswith("sentence-transformers>=")
    )
    torch_dep = next(dependency for dependency in dependencies if dependency.startswith("torch<"))

    assert "sys_platform == 'darwin'" in sentence_transformers_dep
    assert "platform_machine == 'x86_64' and python_version < '3.13'" in sentence_transformers_dep
    assert "platform_machine == 'arm64'" in sentence_transformers_dep
    assert "platform_machine == 'aarch64'" in sentence_transformers_dep
    assert "sys_platform == 'linux'" in sentence_transformers_dep
    assert "platform_machine == 'x86_64'" in sentence_transformers_dep
    assert torch_dep == (
        "torch<2.3; sys_platform == 'darwin' and platform_machine == 'x86_64' "
        "and python_version < '3.13'"
    )
