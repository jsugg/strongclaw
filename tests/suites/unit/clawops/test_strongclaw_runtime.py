from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Any, cast

import pytest

import clawops.strongclaw_runtime as runtime
from clawops.app_paths import strongclaw_varlock_dir
from clawops.memory_profiles import MANAGED_MEMORY_PROFILE_IDS, MEMORY_PROFILES
from clawops.strongclaw_runtime import CommandError, ExecResult, write_env_assignments
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


def test_resolve_varlock_env_mode_defaults_to_auto() -> None:
    assert runtime.resolve_varlock_env_mode(environ={}) == "auto"


def test_resolve_varlock_env_mode_rejects_unknown_value() -> None:
    with pytest.raises(CommandError, match="Unsupported Varlock env mode"):
        runtime.resolve_varlock_env_mode(environ={"OPENCLAW_VARLOCK_ENV_MODE": "invalid"})


def test_varlock_env_dir_uses_managed_mode_when_requested(
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

    managed_root = tmp_path / "config-root"
    test_context.env.set("STRONGCLAW_CONFIG_DIR", str(managed_root))

    resolved = runtime.varlock_env_dir(repo_root, env_mode="managed")

    assert resolved == strongclaw_varlock_dir()
    assert resolved != legacy_dir
    assert (resolved / ".env.schema").read_text(encoding="utf-8") == "APP_ENV=\n"


def test_varlock_env_dir_legacy_mode_requires_legacy_contract(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "assets"
    (repo_root / "platform" / "configs" / "varlock").mkdir(parents=True, exist_ok=True)

    with pytest.raises(CommandError, match="Legacy Varlock env directory not found"):
        runtime.varlock_env_dir(repo_root, env_mode="legacy")


def test_varlock_env_dir_legacy_mode_uses_legacy_contract_when_present(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = tmp_path / "assets"
    legacy_dir = repo_root / "platform" / "configs" / "varlock"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / ".env.local").write_text("APP_ENV=local\n", encoding="utf-8")

    assert runtime.varlock_env_dir(repo_root, env_mode="legacy") == legacy_dir


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


def test_varlock_env_dir_supports_explicit_legacy_mode(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = tmp_path / "assets"
    legacy_dir = repo_root / "platform" / "configs" / "varlock"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / ".env.local").write_text("APP_ENV=local\n", encoding="utf-8")
    test_context.env.set("STRONGCLAW_VARLOCK_ENV_MODE", "legacy")

    expected = runtime.varlock_env_dir(repo_root)

    assert expected == legacy_dir


def test_varlock_env_dir_rejects_missing_legacy_mode_directory(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = tmp_path / "assets"
    test_context.env.set("STRONGCLAW_VARLOCK_ENV_MODE", "legacy")

    with pytest.raises(CommandError, match="Legacy Varlock env directory not found"):
        runtime.varlock_env_dir(repo_root)


def test_use_varlock_env_mode_restores_previous_environment_value(
    test_context: TestContext,
) -> None:
    test_context.env.set("STRONGCLAW_VARLOCK_ENV_MODE", "legacy")

    with runtime.use_varlock_env_mode("managed", default="managed"):
        assert os.environ["STRONGCLAW_VARLOCK_ENV_MODE"] == "managed"

    assert os.environ["STRONGCLAW_VARLOCK_ENV_MODE"] == "legacy"


def test_normalize_varlock_env_mode_rejects_unknown_values() -> None:
    with pytest.raises(CommandError, match="Unsupported Varlock env mode"):
        runtime.normalize_varlock_env_mode("invalid-mode")


def test_managed_python_falls_back_to_current_interpreter(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "assets"

    assert runtime.managed_python(repo_root) == pathlib.Path(sys.executable)


def test_managed_python_prefers_repo_venv_entrypoint_path(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "assets"
    venv_python = repo_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.symlink_to(pathlib.Path(sys.executable))

    assert runtime.managed_python(repo_root) == venv_python


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


def test_resolve_openclaw_config_path_prefers_runtime_root_over_local_env_fallback(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    def _load_env_assignments(_: pathlib.Path) -> dict[str, str]:
        return {
            "OPENCLAW_CONFIG_PATH": str(tmp_path / "local-env" / "openclaw.json"),
            "OPENCLAW_CONFIG": str(tmp_path / "legacy-local-env" / "openclaw.json"),
        }

    test_context.patch.patch_object(
        runtime,
        "load_env_assignments",
        new=_load_env_assignments,
    )

    resolved = runtime.resolve_openclaw_config_path(REPO_ROOT, home_dir=tmp_path / "home")

    assert resolved == runtime_root / ".openclaw" / "openclaw.json"


def test_resolve_openclaw_state_dir_prefers_runtime_root_over_local_env_fallback(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    def _load_env_assignments(_: pathlib.Path) -> dict[str, str]:
        return {"OPENCLAW_STATE_DIR": str(tmp_path / "local-env" / ".openclaw")}

    test_context.patch.patch_object(
        runtime,
        "load_env_assignments",
        new=_load_env_assignments,
    )

    resolved = runtime.resolve_openclaw_state_dir(REPO_ROOT, home_dir=tmp_path / "home")

    assert resolved == runtime_root / ".openclaw"


def test_run_command_inherited_omits_timeout_when_none(
    test_context: TestContext,
) -> None:
    recorded_kwargs: dict[str, Any] = {}

    def _fake_subprocess_run(*args: object, **kwargs: object) -> object:
        del args
        recorded_kwargs.update(cast(dict[str, Any], kwargs))
        return subprocess.CompletedProcess[str](args=["echo"], returncode=0)

    test_context.patch.patch_object(runtime.subprocess, "run", new=_fake_subprocess_run)

    exit_code = runtime.run_command_inherited(["echo", "ready"], timeout_seconds=None)

    assert exit_code == 0
    assert "timeout" not in recorded_kwargs


def test_run_command_inherited_passes_numeric_timeout(
    test_context: TestContext,
) -> None:
    recorded_kwargs: dict[str, Any] = {}

    def _fake_subprocess_run(*args: object, **kwargs: object) -> object:
        del args
        recorded_kwargs.update(cast(dict[str, Any], kwargs))
        return subprocess.CompletedProcess[str](args=["echo"], returncode=0)

    test_context.patch.patch_object(runtime.subprocess, "run", new=_fake_subprocess_run)

    exit_code = runtime.run_command_inherited(["echo", "ready"], timeout_seconds=45)

    assert exit_code == 0
    assert recorded_kwargs["timeout"] == 45


def test_run_openclaw_command_sanitizes_ambient_provider_env_for_local_ollama_baseline(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """OpenClaw wrapper should ignore unrelated cloud env for a local Ollama baseline."""
    env_file = tmp_path / ".env.local"
    write_env_assignments(
        env_file,
        {
            "OPENCLAW_DEFAULT_MODEL": "ollama/deepseek-r1:latest",
            "OPENCLAW_MODEL_FALLBACKS": "",
            "OLLAMA_API_KEY": "ollama-local",
            "OPENCLAW_OLLAMA_MODEL": "deepseek-r1:latest",
            "HYPERMEMORY_EMBEDDING_MODEL": "ollama/nomic-embed-text",
        },
    )
    captured: dict[str, str] = {}

    def _run_varlock_command(
        repo_root: pathlib.Path,
        command: list[str],
        **kwargs: object,
    ) -> ExecResult:
        del repo_root
        env = cast(dict[str, str], kwargs["env"])
        captured.update(env)
        return ExecResult(tuple(command), 0, "", "", 1)

    def _varlock_local_env_file(*_args: object, **_kwargs: object) -> pathlib.Path:
        return env_file

    def _require_openclaw(_context: str) -> None:
        return None

    test_context.patch.patch_object(runtime, "varlock_local_env_file", new=_varlock_local_env_file)
    test_context.patch.patch_object(runtime, "require_openclaw", new=_require_openclaw)
    test_context.patch.patch_object(runtime, "run_varlock_command", new=_run_varlock_command)
    test_context.env.set("OPENAI_API_KEY", "bad-openai")
    test_context.env.set("ANTHROPIC_API_KEY", "bad-anthropic")
    test_context.env.set("ZAI_API_KEY", "bad-zai")
    test_context.env.set("AWS_PROFILE", "bad-aws")
    test_context.env.set("GEMINI_API_KEY", "bad-gemini")
    test_context.env.set("KEEP_ME", "still-here")

    runtime.run_openclaw_command(tmp_path, ["memory", "search", "--query", "ClawOps"])

    assert captured["KEEP_ME"] == "still-here"
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ZAI_API_KEY",
        "AWS_PROFILE",
        "GEMINI_API_KEY",
    ):
        assert key not in captured


def test_run_managed_clawops_command_sanitizes_ambient_provider_env_for_local_ollama_baseline(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Managed ClawOps wrapper should inherit the same sanitized Varlock env."""
    env_file = tmp_path / ".env.local"
    write_env_assignments(
        env_file,
        {
            "OPENCLAW_DEFAULT_MODEL": "ollama/deepseek-r1:latest",
            "OPENCLAW_MODEL_FALLBACKS": "",
            "OLLAMA_API_KEY": "ollama-local",
            "OPENCLAW_OLLAMA_MODEL": "deepseek-r1:latest",
            "HYPERMEMORY_EMBEDDING_MODEL": "ollama/nomic-embed-text",
        },
    )
    captured_command: list[str] = []
    captured_env: dict[str, str] = {}

    def _run_varlock_command(
        repo_root: pathlib.Path,
        command: list[str],
        **kwargs: object,
    ) -> ExecResult:
        del repo_root
        captured_command.extend(command)
        env = cast(dict[str, str], kwargs["env"])
        captured_env.update(env)
        return ExecResult(tuple(command), 0, "", "", 1)

    def _varlock_local_env_file(*_args: object, **_kwargs: object) -> pathlib.Path:
        return env_file

    test_context.patch.patch_object(runtime, "varlock_local_env_file", new=_varlock_local_env_file)
    test_context.patch.patch_object(runtime, "run_varlock_command", new=_run_varlock_command)
    test_context.env.set("OPENAI_API_KEY", "bad-openai")
    test_context.env.set("ANTHROPIC_API_KEY", "bad-anthropic")
    test_context.env.set("ZAI_API_KEY", "bad-zai")
    test_context.env.set("AWS_PROFILE", "bad-aws")
    test_context.env.set("GEMINI_API_KEY", "bad-gemini")
    test_context.env.set("KEEP_ME", "still-here")

    runtime.run_managed_clawops_command(tmp_path, ["hypermemory", "status", "--json"])

    assert captured_command[-4:] == ["-m", "clawops", "hypermemory", "status", "--json"][-4:]
    assert captured_env["KEEP_ME"] == "still-here"
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ZAI_API_KEY",
        "AWS_PROFILE",
        "GEMINI_API_KEY",
    ):
        assert key not in captured_env


def test_profile_requirement_helpers_track_managed_registry_flags() -> None:
    for profile_id in MANAGED_MEMORY_PROFILE_IDS:
        profile = MEMORY_PROFILES[profile_id]
        assert runtime.profile_requires_qmd(profile_id) is profile.installs_qmd
        assert runtime.profile_requires_lossless_claw(profile_id) is profile.installs_lossless_claw
        assert (
            runtime.profile_requires_hypermemory_backend(profile_id)
            is profile.enables_hypermemory_backend
        )
        assert runtime.profile_requires_memory_pro_plugin(profile_id) is profile.installs_memory_pro


def test_profile_requirement_helpers_return_false_for_unknown_profile() -> None:
    unknown = "unknown-profile-name"

    assert runtime.profile_requires_qmd(unknown) is False
    assert runtime.profile_requires_lossless_claw(unknown) is False
    assert runtime.profile_requires_hypermemory_backend(unknown) is False
    assert runtime.profile_requires_memory_pro_plugin(unknown) is False


def test_profile_requirement_helpers_include_non_managed_runtime_profiles() -> None:
    assert runtime.profile_requires_qmd("acp") is True
    assert runtime.profile_requires_lossless_claw("acp") is False
    assert runtime.profile_requires_hypermemory_backend("acp") is False
