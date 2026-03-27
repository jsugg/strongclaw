"""Typed contracts shared by the test infrastructure runtime."""

from __future__ import annotations

from collections.abc import Callable
from os import PathLike
from typing import Literal, Protocol, runtime_checkable

type CleanupFn = Callable[[], None]
type IsolationMode = Literal["isolated", "shared"]
type ProfileValue = str | PathLike[str]
type ProfileOverrideValue = ProfileValue | None
type TestProfileName = Literal[
    "fresh_host_macos_colima",
    "fresh_host_push",
    "model_setup_skip",
    "retry_off",
    "retry_safe",
    "structured_logs",
    "workflow_state",
]


@runtime_checkable
class RuntimeTestContext(Protocol):
    """Minimal runtime contract required by infrastructure-aware helpers."""

    tid: str
    resource_prefix: str
    worker_id: str

    def register_cleanup(self, name: str, fn: CleanupFn) -> None: ...

    def register_patch_cleanup(self, name: str, fn: CleanupFn) -> None: ...

    def register_resource(
        self,
        name: str,
        resource: object,
        *,
        cleanup: CleanupFn | None = None,
        expect_cleanup: bool = True,
    ) -> None: ...
