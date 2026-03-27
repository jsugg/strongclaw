"""Per-test isolation context with resource tracking and deterministic cleanup."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, runtime_checkable

from tests.utils.helpers.identity import get_worker_id, make_resource_prefix, make_test_id

_logger = logging.getLogger(__name__)

type CleanupFn = Callable[[], None]


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
    """Create a typed object store for dataclass defaults."""
    return {}


def _resource_meta_store() -> dict[str, ResourceRecord]:
    """Create a typed resource metadata store for dataclass defaults."""
    return {}


def _cleanup_entries() -> list[tuple[str, CleanupFn]]:
    """Create a typed cleanup stack for dataclass defaults."""
    return []


def _auto_cleanup_fn(resource: object) -> CleanupFn | None:
    """Resolve the preferred zero-argument cleanup function for one resource."""
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
    _cleaned: bool = False

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
        """Register a standalone cleanup action with LIFO teardown semantics."""
        self._cleanup_stack.append((name, fn))

    def get_resource(self, name: str) -> object:
        """Return a previously registered resource."""
        return self.resources[name]

    def cleanup_all(self) -> list[tuple[str, Exception]]:
        """Run all registered cleanup actions in reverse order."""
        if self._cleaned:
            return []
        self._cleaned = True

        errors: list[tuple[str, Exception]] = []
        while self._cleanup_stack:
            name, fn = self._cleanup_stack.pop()
            try:
                fn()
                if name in self._resource_meta:
                    self._resource_meta[name].cleaned = True
            except Exception as exc:  # pragma: no cover - exercised through contract tests.
                _logger.error("Cleanup failed for %s in %s: %s", name, self.tid, exc)
                errors.append((name, exc))
        return errors

    def audit_uncleaned(self) -> list[str]:
        """Return the names of resources that expected cleanup but remain uncleaned."""
        return [
            meta.name
            for meta in self._resource_meta.values()
            if meta.expect_cleanup and not meta.cleaned
        ]
