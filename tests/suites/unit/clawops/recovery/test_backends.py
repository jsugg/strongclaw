"""Unit coverage for recovery backend strategy helpers."""

from __future__ import annotations

import pathlib

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


def test_openclaw_backend_passes_the_archive_path_through_the_output_option(
    tmp_path: pathlib.Path,
) -> None:
    """OpenClaw takes the archive destination via --output, never as a positional argument."""
    calls: list[list[str]] = []

    def fake_run_command(argv: list[str], **_kwargs: object) -> ExecResult:
        calls.append(argv)
        return ExecResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    def fake_which(command: str) -> str | None:
        return command

    backend = OpenClawBackupBackend(which=fake_which, run_command=fake_run_command)
    archive = tmp_path / ".openclaw-20260101-000000.tar.gz.tmp"

    assert backend.create(archive) == (True, None, None)
    assert calls == [["openclaw", "backup", "create", "--output", str(archive)]]


def test_openclaw_backend_backs_up_the_planned_state_root(tmp_path: pathlib.Path) -> None:
    """--home-dir must reach OpenClaw, which otherwise backs up the caller's own ~/.openclaw."""
    environments: list[dict[str, str] | None] = []

    def fake_run_command(
        argv: list[str], *, env: dict[str, str] | None = None, **_kwargs: object
    ) -> ExecResult:
        environments.append(env)
        return ExecResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    def fake_which(command: str) -> str | None:
        return command

    state_dir = tmp_path / "home" / ".openclaw"
    backend = OpenClawBackupBackend(
        which=fake_which, run_command=fake_run_command, state_dir=state_dir
    )

    assert backend.create(tmp_path / "archive.tmp") == (True, None, None)
    environment = environments[0]
    assert environment is not None
    assert environment["OPENCLAW_STATE_DIR"] == str(state_dir)
    assert environment["OPENCLAW_CONFIG_PATH"] == str(state_dir / "openclaw.json")
