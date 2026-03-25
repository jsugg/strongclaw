"""Contracts for the per-test context fixture bootstrap."""

from __future__ import annotations

import ast

from tests.fixtures.repo import REPO_ROOT

_FIXTURE_FILE = REPO_ROOT / "tests" / "fixtures" / "test_context.py"


def test_verify_cleanup_is_autouse() -> None:
    tree = ast.parse(_FIXTURE_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "fixture":
            continue
        for keyword in node.keywords:
            if keyword.arg == "autouse":
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    return
    raise AssertionError("_verify_cleanup must be @pytest.fixture(autouse=True)")


def test_test_context_fixture_is_function_scoped() -> None:
    tree = ast.parse(_FIXTURE_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "test_context":
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "scope":
                    raise AssertionError("test_context must not override function scope.")
        return
    raise AssertionError("test_context fixture not found")
