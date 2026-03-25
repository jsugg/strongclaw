"""Per-test isolation context with resource tracking and deterministic cleanup."""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable

from tests.utils.helpers.identity import get_worker_id, make_resource_prefix, make_test_id

_logger = logging.getLogger(__name__)

type CleanupFn = Callable[[], None]


@dataclasses.dataclass(slots=True)
class ResourceRecord:
    """Audit metadata for a registered resource."""

    name: str
    created_at: float = dataclasses.field(default_factory=time.time)
    expect_cleanup: bool = False
    cleanup_fn: CleanupFn | None = None
    cleaned: bool = False


@dataclasses.dataclass(slots=True)
class TestContext:
    """Per-test isolation contract with tracked resources and cleanup."""

    tid: str = dataclasses.field(default_factory=make_test_id)
    resource_prefix: str = dataclasses.field(default_factory=make_resource_prefix)
    worker_id: str = dataclasses.field(default_factory=get_worker_id)
    nodeid: str = ""
    test_name: str = ""
    start_time: float = dataclasses.field(default_factory=time.time)
    resources: dict[str, object] = dataclasses.field(default_factory=dict)
    notes: dict[str, object] = dataclasses.field(default_factory=dict)
    _resource_meta: dict[str, ResourceRecord] = dataclasses.field(default_factory=dict)
    _cleanup_stack: list[tuple[str, CleanupFn]] = dataclasses.field(default_factory=list)
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
            for method_name in ("close", "cleanup", "delete"):
                method = getattr(resource, method_name, None)
                if callable(method):
                    cleanup_fn = method
                    break

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


TestContext.__test__ = False
