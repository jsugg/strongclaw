"""Per-test environment variable isolation and profile application."""

from __future__ import annotations

import os
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

from tests.plugins.infrastructure.profiles import resolve_profile
from tests.plugins.infrastructure.types import IsolationMode, ProfileOverrideValue, ProfileValue

if TYPE_CHECKING:
    from tests.plugins.infrastructure.context import TestContext

FRAMEWORK_ENV_VARS: frozenset[str] = frozenset({"RESOURCE_PREFIX", "TEST_ID", "WORKER_ID"})


def _normalize_value(value: ProfileValue) -> str:
    return value if isinstance(value, str) else str(value)


class EnvironmentManager:
    """Snapshot, mutate, and restore environment state for a single test."""

    def __init__(self, mode: IsolationMode = "isolated") -> None:
        self.mode = mode
        self._snapshot: dict[str, str] = {}
        self._injected: set[str] = set()
        self._snapshotted = False

    def snapshot(self) -> None:
        """Capture the current process environment before any mutations occur."""
        self._snapshot = os.environ.copy()
        self._snapshotted = True

    def inject(self, **env_vars: str) -> None:
        """Inject named environment variables into the process."""
        self.update(env_vars)

    def set(self, key: str, value: ProfileValue) -> None:
        """Set one environment variable for the current test runtime."""
        self._require_snapshot()
        os.environ[key] = _normalize_value(value)
        self._injected.add(key)

    def update(self, env_vars: Mapping[str, ProfileValue]) -> None:
        """Set several environment variables for the current test runtime."""
        self._require_snapshot()
        for key, value in env_vars.items():
            self.set(key, value)

    def remove(self, key: str, *, raising: bool = False) -> None:
        """Remove one environment variable for the current test runtime."""
        self._require_snapshot()
        if key not in os.environ and raising:
            raise KeyError(key)
        os.environ.pop(key, None)
        self._injected.add(key)

    def inject_framework_vars(self, context: TestContext) -> None:
        """Inject framework-owned environment variables for the current test."""
        self.inject(
            TEST_ID=context.tid,
            RESOURCE_PREFIX=context.resource_prefix,
            WORKER_ID=context.worker_id,
        )

    def apply_profile(
        self,
        name: str,
        *,
        overrides: dict[str, ProfileOverrideValue] | None = None,
    ) -> None:
        """Apply one named environment profile."""
        for key, value in resolve_profile(name).resolve_env(overrides=overrides).items():
            if value is None:
                self.remove(key)
            else:
                self.set(key, value)

    def apply_profiles(self, *names: str) -> None:
        """Apply several named environment profiles in order."""
        for name in names:
            self.apply_profile(name)

    def prepend_path(self, path: str | PathLike[str]) -> None:
        """Prepend one directory to ``PATH`` for the duration of a test."""
        normalized = Path(path).as_posix()
        current = os.environ.get("PATH", "")
        updated = normalized if not current else f"{normalized}{os.pathsep}{current}"
        self.set("PATH", updated)

    def restore(self) -> None:
        """Restore the process environment according to the configured isolation mode."""
        self._require_snapshot()
        if self.mode == "isolated":
            os.environ.clear()
            os.environ.update(self._snapshot)
            self._injected.clear()
            return

        for key in self._injected:
            if key in self._snapshot:
                os.environ[key] = self._snapshot[key]
            else:
                os.environ.pop(key, None)
        self._injected.clear()

    def _require_snapshot(self) -> None:
        if not self._snapshotted:
            raise RuntimeError("EnvironmentManager.snapshot() must run before mutations.")


def register_env_addoption(parser: object) -> None:
    """Register the ``--test-context`` environment isolation option."""
    assert hasattr(parser, "addoption")
    parser.addoption(
        "--test-context",
        choices=["isolated", "shared"],
        default="isolated",
        dest="test_context_mode",
        help="Environment isolation mode for framework-managed test context variables.",
    )
