"""Shared subprocess execution helpers."""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import time
from typing import Mapping, Sequence


@dataclasses.dataclass(slots=True)
class CommandResult:
    """Structured subprocess result."""

    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    failed_to_start: bool = False

    @property
    def ok(self) -> bool:
        """Return True when the command completed successfully."""
        return not self.timed_out and not self.failed_to_start and self.returncode == 0


def run_command(
    command: Sequence[str] | str,
    *,
    cwd: pathlib.Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 30,
    shell: bool = False,
) -> CommandResult:
    """Run a subprocess with explicit shell and timeout semantics."""
    if isinstance(command, str) and not shell:
        raise ValueError("string commands require shell=True")
    if not isinstance(command, str) and shell:
        raise ValueError("shell=True requires a string command")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else dict(env),
            shell=shell,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=None,
            stdout="" if exc.stdout is None else str(exc.stdout),
            stderr="" if exc.stderr is None else str(exc.stderr),
            duration_ms=int((time.perf_counter() - start) * 1000),
            timed_out=True,
        )
    except OSError as exc:
        return CommandResult(
            returncode=None,
            stdout="",
            stderr=str(exc),
            duration_ms=int((time.perf_counter() - start) * 1000),
            failed_to_start=True,
        )

    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
