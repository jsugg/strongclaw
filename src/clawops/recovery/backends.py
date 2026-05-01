"""Recovery backup backend strategies."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Protocol

from clawops.strongclaw_runtime import ExecResult

type WhichFunc = Callable[..., str | bytes | None]
type RunCommandFunc = Callable[..., ExecResult]
type TarWriter = Callable[
    [pathlib.Path],
    None,
]


class BackupBackend(Protocol):
    """Interface for backup backend execution."""

    name: str

    def create(self, archive_tmp_path: pathlib.Path) -> tuple[bool, str | None]:
        """Create one archive and return status + optional failure reason."""
        ...


class OpenClawBackupBackend:
    """OpenClaw CLI backend for backup creation."""

    name = "openclaw-cli"

    def __init__(self, *, which: WhichFunc, run_command: RunCommandFunc) -> None:
        self._which = which
        self._run_command = run_command

    def is_available(self) -> bool:
        """Return whether the OpenClaw executable is available."""
        return self._which("openclaw") is not None

    def create(self, archive_tmp_path: pathlib.Path) -> tuple[bool, str | None]:
        """Create one archive through OpenClaw."""
        result = self._run_command(
            ["openclaw", "backup", "create", str(archive_tmp_path)],
            timeout_seconds=600,
        )
        if result.ok:
            return True, None
        detail = result.stderr.strip() or result.stdout.strip() or "openclaw backup create failed"
        return False, detail


class TarBackupBackend:
    """StrongClaw tar fallback backend."""

    name = "tar-fallback"

    def __init__(self, *, writer: TarWriter) -> None:
        self._writer = writer

    def create(self, archive_tmp_path: pathlib.Path) -> tuple[bool, str | None]:
        """Create one archive through the fallback tar writer."""
        self._writer(archive_tmp_path)
        return True, None
