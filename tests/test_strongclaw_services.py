"""Tests for StrongClaw host service activation helpers."""

from __future__ import annotations

import pytest

from clawops import strongclaw_services
from clawops.strongclaw_runtime import ExecResult


def _result(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> ExecResult:
    """Build one subprocess result for launchd helper tests."""
    return ExecResult(
        argv=("launchctl", "print"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
    )


def test_wait_for_launchd_service_accepts_running_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent launchd services should wait until the daemon is running."""
    responses = iter(
        [
            _result(stdout="state = waiting\nlast exit code = (never exited)\n"),
            _result(stdout="state = running\nlast exit code = (never exited)\n"),
        ]
    )

    monkeypatch.setattr(strongclaw_services, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(strongclaw_services, "run_command", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(strongclaw_services.time, "sleep", lambda _seconds: None)

    strongclaw_services._wait_for_launchd_service(
        strongclaw_services.LAUNCHD_GATEWAY_LABEL,
        persistent=True,
        timeout_seconds=2,
    )


def test_wait_for_launchd_service_accepts_sidecars_after_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-shot launchd services should wait for a clean exit."""
    responses = iter(
        [
            _result(stdout="state = running\nlast exit code = (never exited)\n"),
            _result(stdout="state = waiting\nlast exit code = 0\n"),
        ]
    )

    monkeypatch.setattr(strongclaw_services, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(strongclaw_services, "run_command", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(strongclaw_services.time, "sleep", lambda _seconds: None)

    strongclaw_services._wait_for_launchd_service(
        strongclaw_services.LAUNCHD_SIDECARS_LABEL,
        persistent=False,
        timeout_seconds=2,
    )


def test_wait_for_launchd_service_raises_on_failed_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-shot launchd services should surface non-zero exit codes."""
    monkeypatch.setattr(strongclaw_services, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(
        strongclaw_services,
        "run_command",
        lambda *args, **kwargs: _result(stdout="state = exited\nlast exit code = 78\n"),
    )
    monkeypatch.setattr(strongclaw_services.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="exited with code 78"):
        strongclaw_services._wait_for_launchd_service(
            strongclaw_services.LAUNCHD_SIDECARS_LABEL,
            persistent=False,
            timeout_seconds=2,
        )
