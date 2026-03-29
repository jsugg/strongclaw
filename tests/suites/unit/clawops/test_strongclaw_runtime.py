from __future__ import annotations

import pathlib
import sys

import pytest

import clawops.strongclaw_runtime as runtime
from clawops.app_paths import strongclaw_varlock_dir
from clawops.strongclaw_runtime import CommandError, ExecResult, write_env_assignments
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


def test_write_env_assignments_uses_owner_only_permissions(tmp_path: pathlib.Path) -> None:
    """Env files written by StrongClaw should stay private to the current user."""

    env_file = tmp_path / ".env.local"

    write_env_assignments(env_file, {"OPENCLAW_GATEWAY_TOKEN": "token-value"})

    assert env_file.stat().st_mode & 0o777 == 0o600


def test_docker_backend_diagnostics_capture_runtime_context(
    test_context: TestContext,
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

    test_context.patch.patch_object(runtime, "docker_cli_installed", new=lambda: True)
    test_context.patch.patch_object(runtime, "docker_compose_available", new=lambda: True)
    test_context.patch.patch_object(
        runtime,
        "detect_docker_runtime_provider",
        new=lambda: "OrbStack",
    )
    test_context.patch.patch_object(runtime, "run_command", new=_fake_run_command)
    test_context.env.set("DOCKER_HOST", "unix:///Users/test/.orbstack/run/docker.sock")

    diagnostics = runtime.docker_backend_diagnostics()

    assert diagnostics.backend_ready is False
    assert diagnostics.context == "orbstack"
    assert diagnostics.provider == "OrbStack"
    assert diagnostics.docker_host == "unix:///Users/test/.orbstack/run/docker.sock"
    assert "Cannot connect to the Docker daemon" in diagnostics.info_stderr


def test_ensure_docker_backend_ready_surfaces_runtime_details(
    test_context: TestContext,
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

    test_context.patch.patch_object(runtime, "docker_cli_installed", new=lambda: True)
    test_context.patch.patch_object(runtime, "docker_compose_available", new=lambda: True)
    test_context.patch.patch_object(
        runtime,
        "detect_docker_runtime_provider",
        new=lambda: "OrbStack",
    )
    test_context.patch.patch_object(runtime, "run_command", new=_fake_run_command)
    test_context.patch.patch_object(runtime, "load_docker_refresh_state", new=lambda: None)
    test_context.env.set("DOCKER_HOST", "unix:///Users/test/.orbstack/run/docker.sock")

    with pytest.raises(CommandError) as exc_info:
        runtime.ensure_docker_backend_ready()

    message = str(exc_info.value)
    assert "OrbStack" in message
    assert "orbstack" in message
    assert "Cannot connect to the Docker daemon" in message


def test_varlock_env_dir_defaults_to_managed_config_root(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = tmp_path / "assets"
    asset_dir = repo_root / "platform" / "configs" / "varlock"
    asset_dir.mkdir(parents=True)
    (asset_dir / ".env.local.example").write_text("APP_ENV=local\n", encoding="utf-8")
    (asset_dir / ".env.schema").write_text("APP_ENV=\n", encoding="utf-8")
    (asset_dir / ".env.ci.example").write_text("APP_ENV=ci\n", encoding="utf-8")
    (asset_dir / ".env.prod.example").write_text("APP_ENV=prod\n", encoding="utf-8")
    managed_root = tmp_path / "config-root"
    test_context.env.set("STRONGCLAW_CONFIG_DIR", str(managed_root))
    legacy_dir = repo_root / "platform" / "configs" / "varlock"

    expected = runtime.varlock_env_dir(repo_root)

    assert expected == strongclaw_varlock_dir()
    assert expected != legacy_dir
    assert (expected / ".env.schema").read_text(encoding="utf-8") == "APP_ENV=\n"
    assert (expected / ".env.local.example").read_text(encoding="utf-8") == "APP_ENV=local\n"


def test_varlock_env_dir_ignores_legacy_asset_env_when_runtime_root_is_isolated(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = tmp_path / "assets"
    legacy_dir = repo_root / "platform" / "configs" / "varlock"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / ".env.local").write_text("OPENCLAW_STATE_DIR=~/.openclaw\n", encoding="utf-8")
    (legacy_dir / ".env.local.example").write_text("APP_ENV=local\n", encoding="utf-8")
    (legacy_dir / ".env.schema").write_text("APP_ENV=\n", encoding="utf-8")
    (legacy_dir / ".env.ci.example").write_text("APP_ENV=ci\n", encoding="utf-8")
    (legacy_dir / ".env.prod.example").write_text("APP_ENV=prod\n", encoding="utf-8")
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    expected = runtime.varlock_env_dir(repo_root, home_dir=tmp_path / "home")

    assert expected == runtime_root / "strongclaw" / "config" / "varlock"
    assert expected != legacy_dir
    assert (expected / ".env.local.example").read_text(encoding="utf-8") == "APP_ENV=local\n"


def test_managed_python_falls_back_to_current_interpreter(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "assets"

    assert runtime.managed_python(repo_root) == pathlib.Path(sys.executable).resolve()


def test_resolve_openclaw_config_path_prefers_config_path_env(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    configured_path = tmp_path / "explicit" / "openclaw.json"
    test_context.env.set("OPENCLAW_CONFIG_PATH", str(configured_path))
    test_context.env.set("OPENCLAW_CONFIG", str(tmp_path / "legacy" / "openclaw.json"))

    resolved = runtime.resolve_openclaw_config_path(REPO_ROOT, home_dir=tmp_path / "home")

    assert resolved == configured_path


def test_resolve_openclaw_config_path_uses_runtime_layout_when_isolated(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    resolved = runtime.resolve_openclaw_config_path(REPO_ROOT, home_dir=tmp_path / "home")

    assert resolved == runtime_root / ".openclaw" / "openclaw.json"


def test_resolve_openclaw_state_dir_uses_runtime_layout_when_isolated(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    resolved = runtime.resolve_openclaw_state_dir(REPO_ROOT, home_dir=tmp_path / "home")

    assert resolved == runtime_root / ".openclaw"
