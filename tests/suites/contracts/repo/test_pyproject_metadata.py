"""Tests for project dependency metadata."""

from __future__ import annotations

import tomllib

from tests.fixtures.repo import REPO_ROOT


def _project_dependencies() -> list[str]:
    payload = tomllib.loads(REPO_ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    assert isinstance(dependencies, list)
    return [str(item) for item in dependencies]


def test_rerank_dependency_markers_cover_supported_host_matrix() -> None:
    dependencies = _project_dependencies()
    sentence_transformers_deps = sorted(
        dependency
        for dependency in dependencies
        if dependency.startswith("sentence-transformers==")
    )
    numpy_deps = sorted(
        dependency for dependency in dependencies if dependency.startswith("numpy<")
    )
    torch_deps = sorted(
        dependency for dependency in dependencies if dependency.startswith("torch==")
    )

    assert sentence_transformers_deps == [
        "sentence-transformers==3.4.1; sys_platform == 'darwin' and platform_machine == 'x86_64' and python_version >= '3.12' and python_version < '3.13'",
        "sentence-transformers==5.3.0; ((((sys_platform == 'darwin' and (platform_machine == 'arm64' or platform_machine == 'aarch64')) or (sys_platform == 'linux' and platform_machine == 'x86_64') or (sys_platform == 'linux' and platform_machine == 'aarch64') or (sys_platform == 'linux' and platform_machine == 'arm64')) and python_version >= '3.12' and python_version < '3.14'))",
    ]
    assert numpy_deps == [
        "numpy<2; sys_platform == 'darwin' and platform_machine == 'x86_64' and python_version >= '3.12' and python_version < '3.13'",
    ]
    assert torch_deps == [
        "torch==2.2.2; sys_platform == 'darwin' and platform_machine == 'x86_64' and python_version >= '3.12' and python_version < '3.13'",
        "torch==2.8.0; (((sys_platform == 'darwin' and (platform_machine == 'arm64' or platform_machine == 'aarch64')) or (sys_platform == 'linux' and platform_machine == 'x86_64') or (sys_platform == 'linux' and platform_machine == 'aarch64') or (sys_platform == 'linux' and platform_machine == 'arm64')) and python_version >= '3.12' and python_version < '3.14')",
    ]
