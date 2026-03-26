"""Tests for project dependency metadata."""

from __future__ import annotations

import tomllib

from tests.utils.helpers.repo import REPO_ROOT


def _pyproject() -> dict[str, object]:
    payload = tomllib.loads(REPO_ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _project_dependencies() -> list[str]:
    payload = _pyproject()
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


def test_uv_default_dev_group_models_repo_tooling() -> None:
    payload = _pyproject()
    dependency_groups = payload["dependency-groups"]
    assert isinstance(dependency_groups, dict)
    dev_group = dependency_groups["dev"]
    assert dev_group == [
        "pytest>=8.0.0",
        "pytest-cov>=6.2.1",
        "build>=1.3.0",
        "black>=26.3.1",
        "mypy>=1.19.1",
        "pre-commit>=4.5.1",
        "ruff>=0.15.6",
        "pyright>=1.1.408",
        "isort>=8.0.1",
        "twine>=6.2.0",
        "types-requests>=2.32.4.20260107",
        "types-PyYAML>=6.0.12.20250915",
        "matplotlib-stubs>=0.3.11",
    ]

    tool_config = payload["tool"]
    assert isinstance(tool_config, dict)
    uv_config = tool_config["uv"]
    assert isinstance(uv_config, dict)
    assert uv_config["default-groups"] == ["dev"]

    project = payload["project"]
    assert isinstance(project, dict)
    optional_dependencies = project.get("optional-dependencies")
    assert optional_dependencies is None
