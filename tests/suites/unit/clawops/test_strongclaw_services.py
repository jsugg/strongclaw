"""Tests for StrongClaw host service activation helpers."""

from __future__ import annotations

import pathlib

import pytest

from clawops import strongclaw_services
from clawops.common import load_text
from clawops.strongclaw_runtime import ExecResult
from tests.fixtures.repo import REPO_ROOT


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


def test_render_service_files_includes_launchd_docker_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Rendered launchd plists should inherit the active shell Docker settings."""
    output_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(strongclaw_services, "launchd_dir", lambda: output_dir)
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/docker.sock")
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp/docker&config")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)

    payload = strongclaw_services.render_service_files(
        REPO_ROOT,
        service_manager="launchd",
        state_dir=state_dir,
    )

    assert payload["serviceManager"] == "launchd"
    rendered_sidecars = load_text(output_dir / "ai.openclaw.sidecars.plist")
    assert "<key>DOCKER_HOST</key>" in rendered_sidecars
    assert "<string>unix:///tmp/docker.sock</string>" in rendered_sidecars
    assert "<key>DOCKER_CONFIG</key>" in rendered_sidecars
    assert "<string>/tmp/docker&amp;config</string>" in rendered_sidecars
    assert "<key>DOCKER_CONTEXT</key>" not in rendered_sidecars


def test_render_service_files_omits_launchd_passthrough_env_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Rendered launchd plists should stay unchanged when no Docker env is exported."""
    output_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(strongclaw_services, "launchd_dir", lambda: output_dir)
    for key in strongclaw_services.LAUNCHD_PASSTHROUGH_ENV_VARS:
        monkeypatch.delenv(key, raising=False)

    strongclaw_services.render_service_files(
        REPO_ROOT,
        service_manager="launchd",
        state_dir=state_dir,
    )

    rendered_gateway = load_text(output_dir / "ai.openclaw.gateway.plist")
    assert "__LAUNCHD_EXTRA_ENV__" not in rendered_gateway
    for key in strongclaw_services.LAUNCHD_PASSTHROUGH_ENV_VARS:
        assert f"<key>{key}</key>" not in rendered_gateway


def test_activate_services_retries_launchd_sidecars_after_failed_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Launchd sidecars should be retried once after a transient non-zero exit."""
    output_dir = tmp_path / "LaunchAgents"
    output_dir.mkdir(parents=True)
    payload = {
        "ok": True,
        "serviceManager": "launchd",
        "outputDir": str(output_dir),
        "stateDir": str(tmp_path / "state"),
        "renderedFiles": [],
    }
    calls: list[tuple[str, str]] = []
    wait_attempts = {"sidecars": 0}

    monkeypatch.setattr(
        strongclaw_services, "render_service_files", lambda *args, **kwargs: payload
    )
    monkeypatch.setattr(strongclaw_services, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_services, "_launchd_domain", lambda: "gui/501")

    def fake_activate(domain: str, label: str, _plist: pathlib.Path) -> None:
        calls.append(("activate", f"{domain}:{label}"))

    def fake_wait(label: str, *, persistent: bool, timeout_seconds: int) -> None:
        assert timeout_seconds > 0
        calls.append(("wait", f"{label}:{persistent}"))
        if label == strongclaw_services.LAUNCHD_SIDECARS_LABEL:
            wait_attempts["sidecars"] += 1
            if wait_attempts["sidecars"] == 1:
                raise RuntimeError(f"{label} exited with code 1")

    monkeypatch.setattr(strongclaw_services, "_activate_launchd_service", fake_activate)
    monkeypatch.setattr(strongclaw_services, "_wait_for_launchd_service", fake_wait)
    monkeypatch.setattr(strongclaw_services.time, "sleep", lambda _seconds: None)

    activated = strongclaw_services.activate_services(tmp_path, service_manager="launchd")

    assert activated["activated"] == list(strongclaw_services.LAUNCHD_ACTIVATE_LABELS)
    assert calls == [
        ("activate", "gui/501:ai.openclaw.gateway"),
        ("wait", "ai.openclaw.gateway:True"),
        ("activate", "gui/501:ai.openclaw.sidecars"),
        ("wait", "ai.openclaw.sidecars:False"),
        ("activate", "gui/501:ai.openclaw.sidecars"),
        ("wait", "ai.openclaw.sidecars:False"),
    ]


def test_activate_services_uses_launchd_timeout_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Launchd activation should honor explicit timeout overrides."""
    output_dir = tmp_path / "LaunchAgents"
    output_dir.mkdir(parents=True)
    payload = {
        "ok": True,
        "serviceManager": "launchd",
        "outputDir": str(output_dir),
        "stateDir": str(tmp_path / "state"),
        "renderedFiles": [],
    }
    waits: list[tuple[str, bool, int]] = []

    monkeypatch.setattr(
        strongclaw_services, "render_service_files", lambda *args, **kwargs: payload
    )
    monkeypatch.setattr(strongclaw_services, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_services, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(
        strongclaw_services,
        "_activate_launchd_service",
        lambda _domain, _label, _plist: None,
    )

    def fake_wait(label: str, *, persistent: bool, timeout_seconds: int) -> None:
        waits.append((label, persistent, timeout_seconds))

    monkeypatch.setattr(strongclaw_services, "_wait_for_launchd_service", fake_wait)
    monkeypatch.setenv(strongclaw_services.LAUNCHD_GATEWAY_TIMEOUT_ENV_VAR, "45")
    monkeypatch.setenv(strongclaw_services.LAUNCHD_SIDECARS_TIMEOUT_ENV_VAR, "2700")

    activated = strongclaw_services.activate_services(tmp_path, service_manager="launchd")

    assert activated["activated"] == list(strongclaw_services.LAUNCHD_ACTIVATE_LABELS)
    assert waits == [
        (strongclaw_services.LAUNCHD_GATEWAY_LABEL, True, 45),
        (strongclaw_services.LAUNCHD_SIDECARS_LABEL, False, 2700),
    ]


def test_launchd_timeout_override_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launchd timeout overrides must be positive integers."""
    monkeypatch.setenv("TEST_TIMEOUT", "invalid")
    with pytest.raises(RuntimeError, match="must be a positive integer"):
        strongclaw_services._launchd_timeout_seconds("TEST_TIMEOUT", 30)
