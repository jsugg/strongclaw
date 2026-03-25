"""Per-test environment variable isolation."""

from __future__ import annotations

import os
from typing import Literal

type IsolationMode = Literal["isolated", "shared"]

FRAMEWORK_ENV_VARS: frozenset[str] = frozenset({"TEST_ID", "RESOURCE_PREFIX", "WORKER_ID"})


class EnvironmentManager:
    """Snapshot and restore environment variables around a single test."""

    def __init__(self, mode: IsolationMode = "isolated") -> None:
        self.mode = mode
        self._snapshot: dict[str, str] = {}
        self._injected: set[str] = set()
        self._snapshotted = False

    def snapshot(self) -> None:
        """Capture the current process environment."""
        self._snapshot = os.environ.copy()
        self._snapshotted = True

    def inject(self, **env_vars: str) -> None:
        """Inject framework-managed variables into the process environment."""
        if not self._snapshotted:
            raise RuntimeError("EnvironmentManager.snapshot() must run before inject().")
        for key, value in env_vars.items():
            os.environ[key] = value
            self._injected.add(key)

    def restore(self) -> None:
        """Restore the process environment according to the configured isolation mode."""
        if not self._snapshotted:
            raise RuntimeError("EnvironmentManager.snapshot() must run before restore().")
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
