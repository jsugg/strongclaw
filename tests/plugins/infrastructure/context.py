"""Per-test isolation context with resource tracking and deterministic cleanup."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast, runtime_checkable

import pytest

from tests.plugins.infrastructure.identity import get_worker_id, make_resource_prefix, make_test_id
from tests.plugins.infrastructure.types import CleanupFn

if TYPE_CHECKING:
    from tests.plugins.infrastructure.environment import EnvironmentManager
    from tests.plugins.infrastructure.patching import PatchManager

_logger = logging.getLogger(__name__)


@runtime_checkable
class SupportsClose(Protocol):
    """Resource that exposes a ``close`` teardown method."""

    def close(self) -> None: ...


@runtime_checkable
class SupportsCleanup(Protocol):
    """Resource that exposes a ``cleanup`` teardown method."""

    def cleanup(self) -> None: ...


@runtime_checkable
class SupportsDelete(Protocol):
    """Resource that exposes a ``delete`` teardown method."""

    def delete(self) -> None: ...


def _object_store() -> dict[str, object]:
    return {}


def _resource_meta_store() -> dict[str, ResourceRecord]:
    return {}


def _cleanup_entries() -> list[tuple[str, CleanupFn]]:
    return []


def _auto_cleanup_fn(resource: object) -> CleanupFn | None:
    if isinstance(resource, SupportsClose):
        return resource.close
    if isinstance(resource, SupportsCleanup):
        return resource.cleanup
    if isinstance(resource, SupportsDelete):
        return resource.delete
    return None


@dataclass(slots=True)
class ResourceRecord:
    """Audit metadata for a registered resource."""

    name: str
    created_at: float = field(default_factory=time.time)
    expect_cleanup: bool = False
    cleanup_fn: CleanupFn | None = None
    cleaned: bool = False


@dataclass(slots=True)
class TestContext:
    """Per-test isolation contract with tracked resources and cleanup."""

    __test__: ClassVar[bool] = False

    tid: str = field(default_factory=make_test_id)
    resource_prefix: str = field(default_factory=make_resource_prefix)
    worker_id: str = field(default_factory=get_worker_id)
    nodeid: str = ""
    test_name: str = ""
    start_time: float = field(default_factory=time.time)
    resources: dict[str, object] = field(default_factory=_object_store)
    notes: dict[str, object] = field(default_factory=_object_store)
    _resource_meta: dict[str, ResourceRecord] = field(default_factory=_resource_meta_store)
    _cleanup_stack: list[tuple[str, CleanupFn]] = field(default_factory=_cleanup_entries)
    _patch_cleanup_stack: list[tuple[str, CleanupFn]] = field(default_factory=_cleanup_entries)
    _cleaned: bool = False
    _environment: EnvironmentManager | None = field(default=None, init=False, repr=False)
    _patch_manager: PatchManager | None = field(default=None, init=False, repr=False)
    _cwd_snapshot: str | None = field(default=None, init=False, repr=False)

    def attach_environment(self, manager: EnvironmentManager) -> None:
        """Bind the framework-managed environment runtime to this context."""
        self._environment = manager

    def attach_patch_manager(self, manager: PatchManager) -> None:
        """Bind the framework-managed patch runtime to this context."""
        self._patch_manager = manager

    @property
    def env(self) -> EnvironmentManager:
        """Return the framework-managed environment runtime."""
        if self._environment is None:
            raise RuntimeError("TestContext environment is not initialized.")
        return self._environment

    @property
    def patch(self) -> PatchManager:
        """Return the framework-managed patch runtime."""
        if self._patch_manager is None:
            raise RuntimeError("TestContext patch manager is not initialized.")
        return self._patch_manager

    def apply_profiles(self, *names: str) -> None:
        """Apply one or more named runtime profiles to the current test."""
        self.env.apply_profiles(*names)

    def chdir(self, path: str | os.PathLike[str]) -> None:
        """Change the current working directory and restore it during cleanup."""
        if self._cwd_snapshot is None:
            self._cwd_snapshot = os.getcwd()
            self.register_cleanup("cwd", self._restore_cwd)
        os.chdir(path)

    def register_resource(
        self,
        name: str,
        resource: object,
        *,
        cleanup: CleanupFn | None = None,
        expect_cleanup: bool = True,
    ) -> None:
        """Register a named resource with optional cleanup tracking."""
        if name in self.resources:
            _logger.warning("Resource %s overwritten in test %s", name, self.tid)

        cleanup_fn = cleanup
        if cleanup_fn is None and expect_cleanup:
            cleanup_fn = _auto_cleanup_fn(resource)

        self.resources[name] = resource
        self._resource_meta[name] = ResourceRecord(
            name=name,
            expect_cleanup=expect_cleanup and cleanup_fn is not None,
            cleanup_fn=cleanup_fn,
        )
        if cleanup_fn is not None:
            self._cleanup_stack.append((name, cleanup_fn))

    def register_cleanup(self, name: str, fn: CleanupFn) -> None:
        """Register a standalone cleanup action with LIFO resource semantics."""
        self._cleanup_stack.append((name, fn))

    def register_patch_cleanup(self, name: str, fn: CleanupFn) -> None:
        """Register a patch cleanup action that must run before resource teardown."""
        self._patch_cleanup_stack.append((name, fn))

    def get_resource(self, name: str) -> object:
        """Return a previously registered resource."""
        return self.resources[name]

    def cleanup_all(self) -> list[tuple[str, Exception]]:
        """Run patch cleanup first, then resource cleanup, both in reverse order."""
        if self._cleaned:
            return []
        self._cleaned = True

        errors: list[tuple[str, Exception]] = []
        errors.extend(self._run_cleanup_stack(self._patch_cleanup_stack))
        errors.extend(self._run_cleanup_stack(self._cleanup_stack))
        return errors

    def audit_uncleaned(self) -> list[str]:
        """Return the names of resources that expected cleanup but remain uncleaned."""
        return [
            meta.name
            for meta in self._resource_meta.values()
            if meta.expect_cleanup and not meta.cleaned
        ]

    def _run_cleanup_stack(
        self,
        stack: list[tuple[str, CleanupFn]],
    ) -> list[tuple[str, Exception]]:
        errors: list[tuple[str, Exception]] = []
        while stack:
            name, fn = stack.pop()
            try:
                fn()
                if name in self._resource_meta:
                    self._resource_meta[name].cleaned = True
            except Exception as exc:  # pragma: no cover - exercised through contract tests.
                _logger.error("Cleanup failed for %s in %s: %s", name, self.tid, exc)
                errors.append((name, exc))
        return errors

    def _restore_cwd(self) -> None:
        if self._cwd_snapshot is None:
            return
        os.chdir(self._cwd_snapshot)
        self._cwd_snapshot = None


CONTEXT_KEY = pytest.StashKey[TestContext]()


def current_test_context(request: pytest.FixtureRequest) -> TestContext:
    """Return the current test context from the node stash."""
    node = cast(pytest.Item, cast(Any, request).node)
    context = node.stash.get(CONTEXT_KEY, None)
    if context is None:
        raise RuntimeError(f"TestContext was not initialized for {node.nodeid}")
    return context
