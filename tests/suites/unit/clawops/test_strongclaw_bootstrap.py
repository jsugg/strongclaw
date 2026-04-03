from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest

from clawops import strongclaw_bootstrap
from tests.plugins.infrastructure.context import TestContext


def _mark_source_checkout(repo_root: pathlib.Path) -> None:
    """Create the minimal StrongClaw source markers for bootstrap tests."""
    (repo_root / "platform").mkdir(parents=True)
    (repo_root / "src" / "clawops").mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname = 'clawops'\n", encoding="utf-8")


def test_uv_sync_managed_environment_uses_uv_default_dev_group(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap should rely on uv's default dev group instead of `--extra dev`."""

    uv_binary = tmp_path / "uv"
    seen: dict[str, object] = {}

    def _ensure_uv_installed(**_: object) -> pathlib.Path:
        return uv_binary

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=_ensure_uv_installed,
    )

    def fake_stream_checked(command: list[str], **kwargs: object) -> None:
        seen["command"] = command
        seen["timeout_seconds"] = kwargs["timeout_seconds"]

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=fake_stream_checked,
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _mark_source_checkout(repo_root)

    assert (
        strongclaw_bootstrap.uv_sync_managed_environment(repo_root, home_dir=tmp_path) == uv_binary
    )
    assert seen == {
        "command": [
            str(uv_binary),
            "sync",
            "--project",
            str(repo_root),
            "--python",
            "3.12",
            "--locked",
        ],
        "timeout_seconds": 3600,
    }


def test_uv_sync_managed_environment_retries_transient_failure(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap should retry uv sync after a transient command failure."""

    uv_binary = tmp_path / "uv"
    seen_commands: list[list[str]] = []
    seen_sleeps: list[int] = []

    def _ensure_uv_installed(**_: object) -> pathlib.Path:
        return uv_binary

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=_ensure_uv_installed,
    )

    def fake_stream_checked(command: list[str], **kwargs: object) -> None:
        seen_commands.append(command)
        assert kwargs["timeout_seconds"] == 3600
        if len(seen_commands) == 1:
            raise strongclaw_bootstrap.CommandError("temporary download timeout")

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=fake_stream_checked,
    )
    test_context.patch.patch_object(strongclaw_bootstrap.time, "sleep", new=seen_sleeps.append)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _mark_source_checkout(repo_root)

    assert (
        strongclaw_bootstrap.uv_sync_managed_environment(repo_root, home_dir=tmp_path) == uv_binary
    )
    assert seen_commands == [
        [
            str(uv_binary),
            "sync",
            "--project",
            str(repo_root),
            "--python",
            "3.12",
            "--locked",
        ],
        [
            str(uv_binary),
            "sync",
            "--project",
            str(repo_root),
            "--python",
            "3.12",
            "--locked",
        ],
    ]
    assert seen_sleeps == [5]


def test_uv_sync_managed_environment_raises_after_retry_budget(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap should surface the last uv sync failure after exhausting retries."""

    uv_binary = tmp_path / "uv"
    seen_sleeps: list[int] = []
    call_count = 0

    def _ensure_uv_installed(**_: object) -> pathlib.Path:
        return uv_binary

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=_ensure_uv_installed,
    )

    def fake_stream_checked(command: list[str], **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        assert command == [
            str(uv_binary),
            "sync",
            "--project",
            str(repo_root),
            "--python",
            "3.12",
            "--locked",
        ]
        assert kwargs["timeout_seconds"] == 3600
        raise strongclaw_bootstrap.CommandError("persistent download timeout")

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=fake_stream_checked,
    )
    test_context.patch.patch_object(strongclaw_bootstrap.time, "sleep", new=seen_sleeps.append)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _mark_source_checkout(repo_root)

    with pytest.raises(strongclaw_bootstrap.CommandError, match="persistent download timeout"):
        strongclaw_bootstrap.uv_sync_managed_environment(repo_root, home_dir=tmp_path)
    assert call_count == 3
    assert seen_sleeps == [5, 10]


def test_resolve_node_command_falls_back_to_nodejs(test_context: TestContext) -> None:
    """Prefer `nodejs` when `node` is unavailable."""

    def _command_exists(command_name: str) -> bool:
        return command_name == "nodejs"

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=_command_exists,
    )

    resolve_node_command = cast(
        Callable[[], str | None],
        cast(Any, strongclaw_bootstrap)._resolve_node_command,
    )
    assert callable(resolve_node_command)
    assert resolve_node_command() == "nodejs"


def test_node_satisfies_minimum_uses_resolved_command(test_context: TestContext) -> None:
    """Version checks should use the resolved Node.js executable name."""

    seen_commands: list[list[str]] = []

    def fake_run_command(command: list[str], **_: object) -> SimpleNamespace:
        seen_commands.append(command)
        return SimpleNamespace(ok=True)

    def _resolve_node_command() -> str:
        return "nodejs"

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_resolve_node_command",
        new=_resolve_node_command,
    )
    test_context.patch.patch_object(strongclaw_bootstrap, "run_command", new=fake_run_command)

    node_satisfies_minimum = cast(
        Callable[[], bool],
        cast(Any, strongclaw_bootstrap)._node_satisfies_minimum,
    )
    assert callable(node_satisfies_minimum)
    assert node_satisfies_minimum() is True
    assert seen_commands == [
        [
            "nodejs",
            "-e",
            "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 16) ? 0 : 1);",
        ]
    ]


def test_install_qmd_asset_writes_wrapper_with_resolved_node_command(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """The generated QMD wrapper should invoke the resolved Node.js command."""

    qmd_install_prefix = tmp_path / ".strongclaw" / "qmd"
    qmd_dist_entry = (
        qmd_install_prefix / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
    )
    qmd_dist_entry.parent.mkdir(parents=True, exist_ok=True)
    qmd_dist_entry.write_text("console.log('ok');\n", encoding="utf-8")

    def _resolve_node_command() -> str:
        return "nodejs"

    def _command_exists(command_name: str) -> bool:
        return command_name == "npm"

    def _stream_checked(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def _strongclaw_qmd_install_dir(**_: object) -> pathlib.Path:
        return qmd_install_prefix

    def _run_command(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(ok=True)

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_resolve_node_command",
        new=_resolve_node_command,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=_command_exists,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=_stream_checked,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "strongclaw_qmd_install_dir",
        new=_strongclaw_qmd_install_dir,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "run_command",
        new=_run_command,
    )

    wrapper_path = strongclaw_bootstrap.install_qmd_asset(home_dir=tmp_path)

    assert wrapper_path.read_text(encoding="utf-8").endswith(
        f'exec nodejs "{qmd_dist_entry}" "$@"\n'
    )


def test_bootstrap_host_darwin_happy_path(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Darwin bootstrap should run dependency setup, npm globals, and completion markers."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    recorded_commands: list[list[str]] = []
    recorded_completion: dict[str, str] = {}

    def _platform_system() -> str:
        return "Darwin"

    def _command_exists(command: str) -> bool:
        return command == "brew"

    def _ensure_command_or_brew(_command_name: str, _formula_name: str) -> None:
        return None

    def _ensure_python_runtime_darwin() -> None:
        return None

    def _ensure_node_runtime_darwin() -> None:
        return None

    def _ensure_docker_compatible_runtime(_host_os: str) -> bool:
        return False

    def _ensure_varlock_installed(_version: str) -> pathlib.Path:
        return tmp_path / "varlock"

    def _uv_sync_managed_environment(
        _repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None = None,
    ) -> pathlib.Path:
        del home_dir
        return tmp_path / "uv"

    def _install_profile_assets(
        _repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path | None = None,
    ) -> list[str]:
        del profile, home_dir
        return []

    def _stream_checked(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800,
    ) -> None:
        del cwd, env, timeout_seconds
        recorded_commands.append(list(command))

    def _ensure_common_state_roots(*, home_dir: pathlib.Path | None = None) -> None:
        del home_dir
        return None

    def _render_post_bootstrap_config(
        _repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path,
    ) -> None:
        del profile, home_dir
        return None

    def _run_post_bootstrap_doctor(_repo_root: pathlib.Path) -> None:
        return None

    def _resolve_runtime_user(_repo_root: pathlib.Path) -> str:
        return "solo-dev"

    def _mark_bootstrap_complete(*, profile: str, host_os: str, runtime_user: str) -> None:
        recorded_completion.update(
            {
                "profile": profile,
                "host_os": host_os,
                "runtime_user": runtime_user,
            }
        )

    test_context.patch.patch_object(
        strongclaw_bootstrap.platform,
        "system",
        new=_platform_system,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=_command_exists,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_ensure_command_or_brew",
        new=_ensure_command_or_brew,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_ensure_python_runtime_darwin",
        new=_ensure_python_runtime_darwin,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_ensure_node_runtime_darwin",
        new=_ensure_node_runtime_darwin,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_docker_compatible_runtime",
        new=_ensure_docker_compatible_runtime,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_varlock_installed",
        new=_ensure_varlock_installed,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "uv_sync_managed_environment",
        new=_uv_sync_managed_environment,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "install_profile_assets",
        new=_install_profile_assets,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=_stream_checked,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_common_state_roots",
        new=_ensure_common_state_roots,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_render_post_bootstrap_config",
        new=_render_post_bootstrap_config,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_run_post_bootstrap_doctor",
        new=_run_post_bootstrap_doctor,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "resolve_runtime_user",
        new=_resolve_runtime_user,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "mark_bootstrap_complete",
        new=_mark_bootstrap_complete,
    )

    payload = strongclaw_bootstrap.bootstrap_host(
        repo_root, profile="hypermemory", home_dir=home_dir
    )

    assert payload == {
        "ok": True,
        "profile": "hypermemory",
        "hostOs": "Darwin",
        "runtimeUser": "solo-dev",
        "dockerInstalledByBootstrap": False,
    }
    assert recorded_commands == [
        [
            "npm",
            "install",
            "-g",
            f"openclaw@{strongclaw_bootstrap.DEFAULT_OPENCLAW_VERSION}",
            f"acpx@{strongclaw_bootstrap.DEFAULT_ACPX_VERSION}",
        ]
    ]
    assert recorded_completion == {
        "profile": "hypermemory",
        "host_os": "Darwin",
        "runtime_user": "solo-dev",
    }


def test_bootstrap_host_linux_runs_prerequisites_and_repairs_docker_access(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Linux bootstrap should install prerequisites and repair runtime docker access when needed."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    recorded_commands: list[list[str]] = []
    repair_calls: list[str] = []

    def _platform_system() -> str:
        return "Linux"

    def _command_exists(command: str) -> bool:
        return command in {"sudo", "apt-get", "curl"}

    def _stream_checked(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800,
    ) -> None:
        del cwd, env, timeout_seconds
        recorded_commands.append(list(command))

    def _ensure_node_runtime_linux() -> None:
        return None

    def _ensure_docker_compatible_runtime(_host_os: str) -> bool:
        return True

    def _ensure_varlock_installed(_version: str) -> pathlib.Path:
        return tmp_path / "varlock"

    def _uv_sync_managed_environment(
        _repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None = None,
    ) -> pathlib.Path:
        del home_dir
        return tmp_path / "uv"

    def _install_profile_assets(
        _repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path | None = None,
    ) -> list[str]:
        del profile, home_dir
        return []

    def _ensure_common_state_roots(*, home_dir: pathlib.Path | None = None) -> None:
        del home_dir
        return None

    def _render_post_bootstrap_config(
        _repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path,
    ) -> None:
        del profile, home_dir
        return None

    def _run_post_bootstrap_doctor(_repo_root: pathlib.Path) -> None:
        return None

    def _resolve_runtime_user(_repo_root: pathlib.Path) -> str:
        return "solo-dev"

    def _repair_linux_runtime_user_docker_access(runtime_user: str) -> None:
        repair_calls.append(runtime_user)

    def _mark_bootstrap_complete(*, profile: str, host_os: str, runtime_user: str) -> None:
        del profile, host_os, runtime_user
        return None

    test_context.patch.patch_object(
        strongclaw_bootstrap.platform,
        "system",
        new=_platform_system,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=_command_exists,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=_stream_checked,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_ensure_node_runtime_linux",
        new=_ensure_node_runtime_linux,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_docker_compatible_runtime",
        new=_ensure_docker_compatible_runtime,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_varlock_installed",
        new=_ensure_varlock_installed,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "uv_sync_managed_environment",
        new=_uv_sync_managed_environment,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "install_profile_assets",
        new=_install_profile_assets,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_common_state_roots",
        new=_ensure_common_state_roots,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_render_post_bootstrap_config",
        new=_render_post_bootstrap_config,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_run_post_bootstrap_doctor",
        new=_run_post_bootstrap_doctor,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "resolve_runtime_user",
        new=_resolve_runtime_user,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "repair_linux_runtime_user_docker_access",
        new=_repair_linux_runtime_user_docker_access,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "mark_bootstrap_complete",
        new=_mark_bootstrap_complete,
    )

    payload = strongclaw_bootstrap.bootstrap_host(
        repo_root, profile="openclaw-default", home_dir=home_dir
    )

    assert payload["hostOs"] == "Linux"
    assert payload["dockerInstalledByBootstrap"] is True
    assert repair_calls == ["solo-dev"]
    assert recorded_commands[:2] == [
        ["sudo", "apt-get", "update"],
        [
            "sudo",
            "apt-get",
            "install",
            "-y",
            "python3",
            "python3-pip",
            "sqlite3",
            "curl",
            "unzip",
            "ca-certificates",
            "gnupg",
        ],
    ]
    assert recorded_commands[-1] == [
        "sudo",
        "npm",
        "install",
        "-g",
        f"openclaw@{strongclaw_bootstrap.DEFAULT_OPENCLAW_VERSION}",
        f"acpx@{strongclaw_bootstrap.DEFAULT_ACPX_VERSION}",
    ]
