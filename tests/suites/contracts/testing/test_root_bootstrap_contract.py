"""Contracts for root pytest bootstrap responsibilities."""

from __future__ import annotations

import ast

from tests.utils.helpers.repo import REPO_ROOT

_CONFTEST = REPO_ROOT / "tests" / "conftest.py"


def test_root_conftest_stays_lean() -> None:
    lines = _CONFTEST.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 80, f"Root conftest is {len(lines)} lines (max 80)"


def test_root_conftest_only_assigns_structural_markers() -> None:
    source = _CONFTEST.read_text(encoding="utf-8")
    for marker in ("qdrant", "network_local"):
        assert f"pytest.mark.{marker}" not in source


def test_root_conftest_avoids_fixture_imports() -> None:
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module.startswith("tests.fixtures"):
            raise AssertionError(
                f"Root conftest imports {node.module}; use pytest_plugins instead."
            )


def test_root_conftest_registers_shared_fixture_plugins() -> None:
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    plugin_values: tuple[str, ...] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "pytest_plugins":
            continue
        if not isinstance(node.value, ast.Tuple):
            raise AssertionError("pytest_plugins must be a tuple.")
        plugin_values = tuple(
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
        break

    assert plugin_values == (
        "tests.plugins.infrastructure",
        "tests.fixtures",
    )
