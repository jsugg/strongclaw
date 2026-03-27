"""Framework contracts for recursive pytest fixture-plugin registration."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.utils.helpers.repo import REPO_ROOT


def _extract_pytest_plugins(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "pytest_plugins":
            continue
        if not isinstance(node.value, ast.Tuple):
            raise AssertionError(
                f"{path.relative_to(REPO_ROOT)} must assign a tuple to pytest_plugins"
            )
        return tuple(
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} must define pytest_plugins")


def test_recursive_fixture_plugin_registries_are_explicit() -> None:
    fixture_root = REPO_ROOT / "tests" / "fixtures"
    expected = {
        REPO_ROOT / "tests" / "conftest.py": ("tests.fixtures",),
        fixture_root
        / "__init__.py": (
            "tests.fixtures.core",
            "tests.fixtures.hypermemory",
            "tests.fixtures.platform",
        ),
        fixture_root
        / "core"
        / "__init__.py": (
            "tests.fixtures.core.cli",
            "tests.fixtures.core.context",
            "tests.fixtures.core.test_context",
        ),
        fixture_root / "hypermemory" / "__init__.py": ("tests.fixtures.hypermemory.workspace",),
        fixture_root
        / "platform"
        / "__init__.py": (
            "tests.fixtures.platform.journal",
            "tests.fixtures.platform.network",
            "tests.fixtures.platform.observability",
            "tests.fixtures.platform.policy",
        ),
    }

    for path, plugins in expected.items():
        assert _extract_pytest_plugins(path) == plugins


def test_fixture_leaf_modules_live_under_domain_packages() -> None:
    fixture_root = REPO_ROOT / "tests" / "fixtures"
    top_level_python_files = sorted(
        path.name for path in fixture_root.glob("*.py") if path.name != "__init__.py"
    )
    assert top_level_python_files == []
