"""Pytest fixtures for per-test isolation context and cleanup helpers."""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from typing import cast

import pytest

from tests.utils.helpers.env import EnvironmentManager, IsolationMode
from tests.utils.helpers.patches import PatchManager
from tests.utils.helpers.test_context import TestContext

_CONTEXT_KEY = pytest.StashKey[TestContext]()


@pytest.fixture
def test_context(request: pytest.FixtureRequest) -> Iterator[TestContext]:
    """Create an isolated ``TestContext`` for the current test."""
    ctx = TestContext(nodeid=request.node.nodeid, test_name=request.node.name)
    request.node.stash[_CONTEXT_KEY] = ctx
    yield ctx

    errors = ctx.cleanup_all()
    for name, exc in errors:
        warnings.warn(f"Resource '{name}' cleanup failed: {exc}", stacklevel=1)

    for name in ctx.audit_uncleaned():
        warnings.warn(
            f"Resource '{name}' expected cleanup but was not cleaned in {request.node.nodeid}",
            stacklevel=1,
        )


@pytest.fixture(autouse=True)
def _verify_cleanup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Warn when a test exits with tracked resources that still expect cleanup."""
    yield
    ctx = request.node.stash.get(_CONTEXT_KEY, None)
    if ctx is None:
        return
    uncleaned = ctx.audit_uncleaned()
    if uncleaned:
        warnings.warn(
            f"Uncleaned resources in {request.node.nodeid}: {uncleaned}",
            stacklevel=1,
        )


@pytest.fixture
def env_manager(
    request: pytest.FixtureRequest,
    test_context: TestContext,
) -> Iterator[EnvironmentManager]:
    """Manage framework-owned environment variables around a single test."""
    mode = cast(IsolationMode, request.config.getoption("test_context_mode"))
    manager = EnvironmentManager(mode=mode)
    manager.snapshot()
    manager.inject(
        TEST_ID=test_context.tid,
        RESOURCE_PREFIX=test_context.resource_prefix,
        WORKER_ID=test_context.worker_id,
    )
    try:
        yield manager
    finally:
        manager.restore()


@pytest.fixture
def patch_manager(test_context: TestContext) -> PatchManager:
    """Return a tracked patch manager bound to the current test context."""
    return PatchManager(test_context)


__all__ = [
    "EnvironmentManager",
    "PatchManager",
    "TestContext",
    "env_manager",
    "patch_manager",
    "test_context",
]
