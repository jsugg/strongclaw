"""Recovery backup backend strategies."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Callable
from typing import Protocol

from clawops.strongclaw_runtime import ExecResult

type WhichFunc = Callable[..., str | bytes | None]
type RunCommandFunc = Callable[..., ExecResult]
type TarWriter = Callable[
    [pathlib.Path],
    dict[str, object],
]


class BackupBackend(Protocol):
    """Interface for backup backend execution."""

    name: str

    def create(
        self, archive_tmp_path: pathlib.Path
    ) -> tuple[bool, str | None, dict[str, object] | None]:
        """Create one archive and return status + optional failure reason."""
        ...


class OpenClawBackupBackend:
    """OpenClaw CLI backend for backup creation."""

    name = "openclaw-cli"

    def __init__(
        self,
        *,
        which: WhichFunc,
        run_command: RunCommandFunc,
        state_dir: pathlib.Path | None = None,
    ) -> None:
        self._which = which
        self._run_command = run_command
        self._state_dir = state_dir

    def is_available(self) -> bool:
        """Return whether the OpenClaw executable is available."""
        return self._which("openclaw") is not None

    def create(
        self, archive_tmp_path: pathlib.Path
    ) -> tuple[bool, str | None, dict[str, object] | None]:
        """Create one archive through OpenClaw."""
        result = self._run_command(
            ["openclaw", "backup", "create", "--output", str(archive_tmp_path)],
            env=self._environment(),
            timeout_seconds=600,
        )
        if result.ok:
            return True, None, None
        detail = result.stderr.strip() or result.stdout.strip() or "openclaw backup create failed"
        return False, detail, None

    def _environment(self) -> dict[str, str] | None:
        """Point OpenClaw at the planned state root instead of the caller's own home."""
        if self._state_dir is None:
            return None
        return {
            **os.environ,
            "OPENCLAW_STATE_DIR": str(self._state_dir),
            "OPENCLAW_CONFIG_PATH": str(self._state_dir / "openclaw.json"),
        }


class TarBackupBackend:
    """StrongClaw tar fallback backend."""

    name = "tar-fallback"

    def __init__(self, *, writer: TarWriter) -> None:
        self._writer = writer

    def create(
        self, archive_tmp_path: pathlib.Path
    ) -> tuple[bool, str | None, dict[str, object] | None]:
        """Create one archive through the fallback tar writer."""
        manifest = self._writer(archive_tmp_path)
        return True, None, manifest
