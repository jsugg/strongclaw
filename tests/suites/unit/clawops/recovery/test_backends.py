"""Unit coverage for recovery backend strategy helpers."""

from __future__ import annotations

from clawops.recovery.backends import OpenClawBackupBackend
from clawops.strongclaw_runtime import ExecResult


def test_openclaw_backend_availability_uses_standard_which_signature() -> None:
    """Availability checks should call which() with only the command string."""

    def fake_which(command: str) -> str | None:
        if command == "openclaw":
            return "/usr/local/bin/openclaw"
        return None

    def fake_run_command(*_args: object, **_kwargs: object) -> ExecResult:
        return ExecResult(
            argv=("openclaw", "backup", "create"),
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=1,
        )

    backend = OpenClawBackupBackend(which=fake_which, run_command=fake_run_command)
    assert backend.is_available() is True
