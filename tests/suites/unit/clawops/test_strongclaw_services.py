"""Tests for StrongClaw host service activation helpers."""

from __future__ import annotations

import pathlib
from typing import Any, Protocol, cast

import pytest

from clawops import strongclaw_services
from clawops.common import load_text
from clawops.strongclaw_runtime import ExecResult
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


class _WaitForLaunchdService(Protocol):
    def __call__(self, label: str, *, persistent: bool, timeout_seconds: int) -> None: ...


class _LaunchdTimeoutSeconds(Protocol):
    def __call__(self, env_var: str, default: int) -> int: ...


def _result(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> ExecResult:
    """Build one subprocess result for launchd helper tests."""
    return ExecResult(
        argv=("launchctl", "print"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
    )


def test_wait_for_launchd_service_accepts_running_gateway(test_context: TestContext) -> None:
    """Persistent launchd services should wait until the daemon is running."""
    responses = iter(
        [
            _result(stdout="state = waiting\nlast exit code = (never exited)\n"),
            _result(stdout="state = running\nlast exit code = (never exited)\n"),
        ]
    )

    def _launchd_domain() -> str:
        return "gui/501"

    def _run_command(*args: object, **kwargs: object) -> ExecResult:
        del args, kwargs
        return next(responses)

    def _sleep(_seconds: float) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "run_command",
        new=_run_command,
    )
    test_context.patch.patch_object(strongclaw_services.time, "sleep", new=_sleep)

    wait_for_launchd_service = cast(
        _WaitForLaunchdService,
        cast(Any, strongclaw_services)._wait_for_launchd_service,
    )
    assert callable(wait_for_launchd_service)
    wait_for_launchd_service(
        strongclaw_services.LAUNCHD_GATEWAY_LABEL,
        persistent=True,
        timeout_seconds=2,
    )


def test_wait_for_launchd_service_accepts_sidecars_after_zero_exit(
    test_context: TestContext,
) -> None:
    """One-shot launchd services should wait for a clean exit."""
    responses = iter(
        [
            _result(stdout="state = running\nlast exit code = (never exited)\n"),
            _result(stdout="state = waiting\nlast exit code = 0\n"),
        ]
    )

    def _launchd_domain() -> str:
        return "gui/501"

    def _run_command(*args: object, **kwargs: object) -> ExecResult:
        del args, kwargs
        return next(responses)

    def _sleep(_seconds: float) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "run_command",
        new=_run_command,
    )
    test_context.patch.patch_object(strongclaw_services.time, "sleep", new=_sleep)

    wait_for_launchd_service = cast(
        _WaitForLaunchdService,
        cast(Any, strongclaw_services)._wait_for_launchd_service,
    )
    assert callable(wait_for_launchd_service)
    wait_for_launchd_service(
        strongclaw_services.LAUNCHD_SIDECARS_LABEL,
        persistent=False,
        timeout_seconds=2,
    )


def test_wait_for_launchd_service_raises_on_failed_exit(
    test_context: TestContext,
) -> None:
    """One-shot launchd services should surface non-zero exit codes."""

    def _launchd_domain() -> str:
        return "gui/501"

    def _run_command(*args: object, **kwargs: object) -> ExecResult:
        del args, kwargs
        return _result(stdout="state = exited\nlast exit code = 78\n")

    def _sleep(_seconds: float) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "run_command",
        new=_run_command,
    )
    test_context.patch.patch_object(strongclaw_services.time, "sleep", new=_sleep)

    with pytest.raises(RuntimeError, match="exited with code 78"):
        wait_for_launchd_service = cast(
            _WaitForLaunchdService,
            cast(Any, strongclaw_services)._wait_for_launchd_service,
        )
        assert callable(wait_for_launchd_service)
        wait_for_launchd_service(
            strongclaw_services.LAUNCHD_SIDECARS_LABEL,
            persistent=False,
            timeout_seconds=2,
        )


def test_wait_for_launchd_service_retries_transient_launchctl_print_failures(
    test_context: TestContext,
) -> None:
    """Transient launchctl print failures should be retried until the deadline."""
    responses = iter(
        [
            _result(returncode=113),
            _result(stdout="state = running\nlast exit code = (never exited)\n"),
            _result(stdout="state = waiting\nlast exit code = 0\n"),
        ]
    )

    def _launchd_domain() -> str:
        return "gui/501"

    def _run_command(*args: object, **kwargs: object) -> ExecResult:
        del args, kwargs
        return next(responses)

    def _sleep(_seconds: float) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "run_command",
        new=_run_command,
    )
    test_context.patch.patch_object(strongclaw_services.time, "sleep", new=_sleep)

    wait_for_launchd_service = cast(
        _WaitForLaunchdService,
        cast(Any, strongclaw_services)._wait_for_launchd_service,
    )
    assert callable(wait_for_launchd_service)
    wait_for_launchd_service(
        strongclaw_services.LAUNCHD_SIDECARS_LABEL,
        persistent=False,
        timeout_seconds=3,
    )


def test_render_service_files_includes_launchd_docker_env(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Rendered launchd plists should inherit the active shell Docker settings."""
    output_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "state"
    test_context.patch.patch_object(strongclaw_services, "launchd_dir", new=lambda: output_dir)
    test_context.env.update(
        {
            "DOCKER_HOST": "unix:///tmp/docker.sock",
            "DOCKER_CONFIG": "/tmp/docker&config",
            "STRONGCLAW_COMPOSE_VARIANT": "ci-hosted-macos",
        }
    )
    test_context.env.remove("DOCKER_CONTEXT")

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
    assert "<key>STRONGCLAW_COMPOSE_VARIANT</key>" in rendered_sidecars
    assert "<string>ci-hosted-macos</string>" in rendered_sidecars


def test_render_service_files_include_isolated_runtime_env(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Rendered services should encode the full isolated runtime contract."""
    runtime_root = tmp_path / "dev-runtime"
    output_dir = tmp_path / "systemd"
    test_context.patch.patch_object(strongclaw_services, "systemd_dir", new=lambda: output_dir)
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    payload = strongclaw_services.render_service_files(REPO_ROOT, service_manager="systemd")
    rendered_gateway = load_text(output_dir / "openclaw-gateway.service")

    assert payload["stateDir"] == str(runtime_root / ".openclaw")
    assert f"Environment=OPENCLAW_HOME={runtime_root}" in rendered_gateway
    assert (
        f"Environment=OPENCLAW_CONFIG_PATH={runtime_root / '.openclaw' / 'openclaw.json'}"
        in rendered_gateway
    )
    assert "Environment=OPENCLAW_PROFILE=strongclaw-dev" in rendered_gateway
    assert f"Environment=STRONGCLAW_RUNTIME_ROOT={runtime_root}" in rendered_gateway


def test_render_service_files_includes_maintenance_timer_and_service(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "systemd"
    test_context.patch.patch_object(strongclaw_services, "systemd_dir", new=lambda: output_dir)

    payload = strongclaw_services.render_service_files(REPO_ROOT, service_manager="systemd")

    assert payload["serviceManager"] == "systemd"
    assert (output_dir / "openclaw-maintenance.service").exists()
    assert (output_dir / "openclaw-maintenance.timer").exists()


def test_render_service_files_omits_launchd_passthrough_env_when_unset(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Rendered launchd plists should stay unchanged when no Docker env is exported."""
    output_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "state"
    test_context.patch.patch_object(strongclaw_services, "launchd_dir", new=lambda: output_dir)
    for key in strongclaw_services.LAUNCHD_PASSTHROUGH_ENV_VARS:
        test_context.env.remove(key)

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
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Launchd sidecars should be retried once after a transient non-zero exit."""
    output_dir = tmp_path / "LaunchAgents"
    output_dir.mkdir(parents=True)
    payload: dict[str, object] = {
        "ok": True,
        "serviceManager": "launchd",
        "outputDir": str(output_dir),
        "stateDir": str(tmp_path / "state"),
        "renderedFiles": [],
    }
    calls: list[tuple[str, str]] = []
    wait_attempts = {"sidecars": 0}

    def _render_service_files(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return payload

    def _ensure_docker_backend_ready() -> None:
        return None

    def _launchd_domain() -> str:
        return "gui/501"

    def _sleep(_seconds: float) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "render_service_files",
        new=_render_service_files,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "ensure_docker_backend_ready",
        new=_ensure_docker_backend_ready,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )

    def fake_activate(domain: str, label: str, _plist: pathlib.Path) -> None:
        calls.append(("activate", f"{domain}:{label}"))

    def fake_wait(label: str, *, persistent: bool, timeout_seconds: int) -> None:
        assert timeout_seconds > 0
        calls.append(("wait", f"{label}:{persistent}"))
        if label == strongclaw_services.LAUNCHD_SIDECARS_LABEL:
            wait_attempts["sidecars"] += 1
            if wait_attempts["sidecars"] == 1:
                raise RuntimeError(f"{label} exited with code 1")

    test_context.patch.patch_object(
        strongclaw_services,
        "_activate_launchd_service",
        new=fake_activate,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "_wait_for_launchd_service",
        new=fake_wait,
    )
    test_context.patch.patch_object(strongclaw_services.time, "sleep", new=_sleep)

    activated = strongclaw_services.activate_services(tmp_path, service_manager="launchd")

    assert activated["activated"] == list(strongclaw_services.LAUNCHD_ACTIVATE_LABELS)
    assert calls == [
        ("activate", "gui/501:ai.openclaw.sidecars"),
        ("wait", "ai.openclaw.sidecars:False"),
        ("activate", "gui/501:ai.openclaw.sidecars"),
        ("wait", "ai.openclaw.sidecars:False"),
        ("activate", "gui/501:ai.openclaw.gateway"),
        ("wait", "ai.openclaw.gateway:True"),
        ("activate", "gui/501:ai.openclaw.backup-create"),
        ("activate", "gui/501:ai.openclaw.backup-verify"),
        ("activate", "gui/501:ai.openclaw.maintenance"),
    ]


def test_activate_services_uses_launchd_timeout_overrides(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Launchd activation should honor explicit timeout overrides."""
    output_dir = tmp_path / "LaunchAgents"
    output_dir.mkdir(parents=True)
    payload: dict[str, object] = {
        "ok": True,
        "serviceManager": "launchd",
        "outputDir": str(output_dir),
        "stateDir": str(tmp_path / "state"),
        "renderedFiles": [],
    }
    waits: list[tuple[str, bool, int]] = []

    def _render_service_files(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return payload

    def _ensure_docker_backend_ready() -> None:
        return None

    def _launchd_domain() -> str:
        return "gui/501"

    def _activate_launchd_service(
        _domain: str,
        _label: str,
        _plist: pathlib.Path,
    ) -> None:
        return None

    test_context.patch.patch_object(
        strongclaw_services,
        "render_service_files",
        new=_render_service_files,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "ensure_docker_backend_ready",
        new=_ensure_docker_backend_ready,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "_launchd_domain",
        new=_launchd_domain,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "_activate_launchd_service",
        new=_activate_launchd_service,
    )

    def fake_wait(label: str, *, persistent: bool, timeout_seconds: int) -> None:
        waits.append((label, persistent, timeout_seconds))

    test_context.patch.patch_object(
        strongclaw_services,
        "_wait_for_launchd_service",
        new=fake_wait,
    )
    test_context.env.update(
        {
            strongclaw_services.LAUNCHD_GATEWAY_TIMEOUT_ENV_VAR: "45",
            strongclaw_services.LAUNCHD_SIDECARS_TIMEOUT_ENV_VAR: "2700",
        }
    )

    activated = strongclaw_services.activate_services(tmp_path, service_manager="launchd")

    assert activated["activated"] == list(strongclaw_services.LAUNCHD_ACTIVATE_LABELS)
    assert waits == [
        (strongclaw_services.LAUNCHD_SIDECARS_LABEL, False, 2700),
        (strongclaw_services.LAUNCHD_GATEWAY_LABEL, True, 45),
    ]


def test_activate_services_enables_systemd_units_in_declared_order(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    payload: dict[str, object] = {
        "ok": True,
        "serviceManager": "systemd",
        "outputDir": str(tmp_path / "systemd"),
        "stateDir": str(tmp_path / "state"),
        "renderedFiles": [],
    }
    observed_commands: list[tuple[str, ...]] = []
    observed_events: list[tuple[str, dict[str, object]]] = []

    def _render_service_files(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return payload

    def _run_command(command: list[str], *, timeout_seconds: int = 30) -> ExecResult:
        del timeout_seconds
        observed_commands.append(tuple(command))
        return ExecResult(
            argv=tuple(command),
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=1,
        )

    test_context.patch.patch_object(
        strongclaw_services,
        "render_service_files",
        new=_render_service_files,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "ensure_docker_backend_ready",
        new=lambda: None,
    )
    test_context.patch.patch_object(
        strongclaw_services,
        "run_command",
        new=_run_command,
    )

    def _emit_structured_log(event: str, payload: object) -> None:
        observed_events.append((str(event), cast(dict[str, object], payload)))

    test_context.patch.patch_object(
        strongclaw_services,
        "emit_structured_log",
        new=_emit_structured_log,
    )

    payload_out = strongclaw_services.activate_services(tmp_path, service_manager="systemd")

    assert payload_out["activated"] == list(strongclaw_services.SYSTEMD_ACTIVATE_UNITS)
    assert observed_commands == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", "openclaw-sidecars.service"),
        ("systemctl", "--user", "enable", "--now", "openclaw-gateway.service"),
        ("systemctl", "--user", "enable", "--now", "openclaw-backup-create.timer"),
        ("systemctl", "--user", "enable", "--now", "openclaw-backup-verify.timer"),
        ("systemctl", "--user", "enable", "--now", "openclaw-maintenance.timer"),
    ]
    assert observed_events[0] == (
        "clawops.services.activate",
        {"service_manager": "systemd", "step": "daemon_reload"},
    )
    assert [event[1].get("unit") for event in observed_events[1:]] == [
        "openclaw-sidecars.service",
        "openclaw-gateway.service",
        "openclaw-backup-create.timer",
        "openclaw-backup-verify.timer",
        "openclaw-maintenance.timer",
    ]


def test_launchd_timeout_override_rejects_invalid_values(test_context: TestContext) -> None:
    """Launchd timeout overrides must be positive integers."""
    test_context.env.set("TEST_TIMEOUT", "invalid")
    with pytest.raises(RuntimeError, match="must be a positive integer"):
        launchd_timeout_seconds = cast(
            _LaunchdTimeoutSeconds,
            cast(Any, strongclaw_services)._launchd_timeout_seconds,
        )
        assert callable(launchd_timeout_seconds)
        launchd_timeout_seconds("TEST_TIMEOUT", 30)
