from __future__ import annotations

import pathlib
import sys

import pytest

import clawops.strongclaw_runtime as runtime
from clawops.app_paths import strongclaw_varlock_dir
from clawops.strongclaw_runtime import CommandError, ExecResult, write_env_assignments


def test_write_env_assignments_uses_owner_only_permissions(tmp_path: pathlib.Path) -> None:
    """Env files written by StrongClaw should stay private to the current user."""

    env_file = tmp_path / ".env.local"

    write_env_assignments(env_file, {"OPENCLAW_GATEWAY_TOKEN": "token-value"})

    assert env_file.stat().st_mode & 0o777 == 0o600


def test_docker_backend_diagnostics_capture_runtime_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run_command(argv: list[str], timeout_seconds: int = 15) -> ExecResult:
        if argv == ["docker", "context", "show"]:
            return ExecResult(tuple(argv), 0, "orbstack\n", "", 4)
        if argv == ["docker", "info"]:
            return ExecResult(
                tuple(argv),
                1,
                "",
                "Cannot connect to the Docker daemon at unix:///Users/test/.orbstack/run/docker.sock",
                12,
            )
        raise AssertionError(argv)

    monkeypatch.setattr(runtime, "docker_cli_installed", lambda: True)
    monkeypatch.setattr(runtime, "docker_compose_available", lambda: True)
    monkeypatch.setattr(runtime, "detect_docker_runtime_provider", lambda: "OrbStack")
    monkeypatch.setattr(runtime, "run_command", _fake_run_command)
    monkeypatch.setenv("DOCKER_HOST", "unix:///Users/test/.orbstack/run/docker.sock")

    diagnostics = runtime.docker_backend_diagnostics()

    assert diagnostics.backend_ready is False
    assert diagnostics.context == "orbstack"
    assert diagnostics.provider == "OrbStack"
    assert diagnostics.docker_host == "unix:///Users/test/.orbstack/run/docker.sock"
    assert "Cannot connect to the Docker daemon" in diagnostics.info_stderr


def test_ensure_docker_backend_ready_surfaces_runtime_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run_command(argv: list[str], timeout_seconds: int = 15) -> ExecResult:
        if argv == ["docker", "context", "show"]:
            return ExecResult(tuple(argv), 0, "orbstack\n", "", 4)
        if argv == ["docker", "info"]:
            return ExecResult(
                tuple(argv),
                1,
                "",
                "Cannot connect to the Docker daemon at unix:///Users/test/.orbstack/run/docker.sock",
                12,
            )
        raise AssertionError(argv)

    monkeypatch.setattr(runtime, "docker_cli_installed", lambda: True)
    monkeypatch.setattr(runtime, "docker_compose_available", lambda: True)
    monkeypatch.setattr(runtime, "detect_docker_runtime_provider", lambda: "OrbStack")
    monkeypatch.setattr(runtime, "run_command", _fake_run_command)
    monkeypatch.setattr(runtime, "load_docker_refresh_state", lambda: None)
    monkeypatch.setenv("DOCKER_HOST", "unix:///Users/test/.orbstack/run/docker.sock")

    with pytest.raises(CommandError) as exc_info:
        runtime.ensure_docker_backend_ready()

    message = str(exc_info.value)
    assert "OrbStack" in message
    assert "orbstack" in message
    assert "Cannot connect to the Docker daemon" in message


def test_varlock_env_dir_defaults_to_managed_config_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "assets"
    (repo_root / "platform" / "configs" / "varlock").mkdir(parents=True)
    managed_root = tmp_path / "config-root"
    monkeypatch.setenv("STRONGCLAW_CONFIG_DIR", str(managed_root))
    legacy_dir = repo_root / "platform" / "configs" / "varlock"

    expected = runtime.varlock_env_dir(repo_root)

    assert expected == strongclaw_varlock_dir()
    assert expected != legacy_dir


def test_managed_python_falls_back_to_current_interpreter(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "assets"

    assert runtime.managed_python(repo_root) == pathlib.Path(sys.executable).resolve()
