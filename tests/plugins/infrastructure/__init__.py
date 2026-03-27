"""Pytest bootstrap for the Strongclaw test infrastructure runtime."""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from typing import cast

import pytest

from tests.plugins.infrastructure.context import CONTEXT_KEY, TestContext, current_test_context
from tests.plugins.infrastructure.environment import EnvironmentManager, register_env_addoption
from tests.plugins.infrastructure.mode import register_mock_addoption
from tests.plugins.infrastructure.patching import PatchManager
from tests.plugins.infrastructure.types import IsolationMode


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register infrastructure-owned CLI options."""
    register_env_addoption(parser)
    register_mock_addoption(parser)


def pytest_configure(config: pytest.Config) -> None:
    """Register infrastructure-owned markers."""
    config.addinivalue_line(
        "markers",
        "test_profile(*names): apply named Strongclaw test infrastructure profiles.",
    )


def _iter_declared_profiles(node: pytest.Node) -> tuple[str, ...]:
    declared: list[str] = []
    for marker in node.iter_markers("test_profile"):
        for value in marker.args:
            if not isinstance(value, str):
                raise TypeError("test_profile marker arguments must be strings.")
            declared.append(value)
    return tuple(declared)


@pytest.fixture(autouse=True)
def _test_infrastructure_runtime(request: pytest.FixtureRequest) -> Iterator[TestContext]:
    """Create a universal ``TestContext`` and bind core runtime services to it."""
    ctx = TestContext(nodeid=request.node.nodeid, test_name=request.node.name)
    env_mode = cast(IsolationMode, request.config.getoption("test_context_mode"))
    env_manager = EnvironmentManager(mode=env_mode)
    env_manager.snapshot()
    env_manager.inject_framework_vars(ctx)
    for profile_name in _iter_declared_profiles(request.node):
        env_manager.apply_profile(profile_name)

    patch_manager = PatchManager(ctx)
    ctx.attach_environment(env_manager)
    ctx.attach_patch_manager(patch_manager)
    request.node.stash[CONTEXT_KEY] = ctx

    try:
        yield ctx
    finally:
        errors = ctx.cleanup_all()
        env_manager.restore()
        for name, exc in errors:
            warnings.warn(f"Resource '{name}' cleanup failed: {exc}", stacklevel=1)

        uncleaned = ctx.audit_uncleaned()
        if uncleaned:
            warnings.warn(
                f"Uncleaned resources in {request.node.nodeid}: {uncleaned}",
                stacklevel=1,
            )


@pytest.fixture
def test_context(request: pytest.FixtureRequest) -> TestContext:
    """Return the universal runtime context for the current test."""
    return current_test_context(request)


__all__ = [
    "CONTEXT_KEY",
    "EnvironmentManager",
    "PatchManager",
    "TestContext",
    "test_context",
]
