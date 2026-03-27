"""Contracts for the universal test-context infrastructure bootstrap."""

from __future__ import annotations

import ast
from typing import Any, cast

import pytest

from tests.plugins.infrastructure import TestContext
from tests.plugins.infrastructure.context import CONTEXT_KEY
from tests.utils.helpers.repo import REPO_ROOT

_PLUGIN_FILE = REPO_ROOT / "tests" / "plugins" / "infrastructure" / "__init__.py"
_FIXTURE_REGISTRY = REPO_ROOT / "tests" / "fixtures" / "core" / "__init__.py"


def test_runtime_fixture_is_autouse() -> None:
    tree = ast.parse(_PLUGIN_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "fixture":
            continue
        for keyword in node.keywords:
            if keyword.arg == "autouse":
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    return
    raise AssertionError("_test_infrastructure_runtime must be @pytest.fixture(autouse=True)")


def test_test_context_fixture_is_accessed_from_stash(
    request: pytest.FixtureRequest,
    test_context: TestContext,
) -> None:
    node = cast(pytest.Item, cast(Any, request).node)
    assert node.stash[CONTEXT_KEY] is test_context
    assert test_context.nodeid == node.nodeid
    assert test_context.test_name == node.name


def test_test_context_always_exposes_env_and_patch_runtime(test_context: TestContext) -> None:
    assert test_context.env is not None
    assert test_context.patch is not None


def test_fixture_registry_no_longer_loads_legacy_test_context_plugin() -> None:
    source = _FIXTURE_REGISTRY.read_text(encoding="utf-8")
    assert "tests.fixtures.core.test_context" not in source
