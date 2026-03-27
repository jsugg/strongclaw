from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from clawops import strongclaw_bootstrap
from tests.plugins.infrastructure.context import TestContext


def test_uv_sync_managed_environment_uses_uv_default_dev_group(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap should rely on uv's default dev group instead of `--extra dev`."""

    uv_binary = tmp_path / "uv"
    seen: dict[str, object] = {}

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=lambda **_: uv_binary,
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

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=lambda **_: uv_binary,
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

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "ensure_uv_installed",
        new=lambda **_: uv_binary,
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

    with pytest.raises(strongclaw_bootstrap.CommandError, match="persistent download timeout"):
        strongclaw_bootstrap.uv_sync_managed_environment(repo_root, home_dir=tmp_path)
    assert call_count == 3
    assert seen_sleeps == [5, 10]


def test_resolve_node_command_falls_back_to_nodejs(test_context: TestContext) -> None:
    """Prefer `nodejs` when `node` is unavailable."""

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=lambda command_name: command_name == "nodejs",
    )

    assert strongclaw_bootstrap._resolve_node_command() == "nodejs"


def test_node_satisfies_minimum_uses_resolved_command(test_context: TestContext) -> None:
    """Version checks should use the resolved Node.js executable name."""

    seen_commands: list[list[str]] = []

    def fake_run_command(command: list[str], **_: object) -> SimpleNamespace:
        seen_commands.append(command)
        return SimpleNamespace(ok=True)

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_resolve_node_command",
        new=lambda: "nodejs",
    )
    test_context.patch.patch_object(strongclaw_bootstrap, "run_command", new=fake_run_command)

    assert strongclaw_bootstrap._node_satisfies_minimum() is True
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

    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_resolve_node_command",
        new=lambda: "nodejs",
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "command_exists",
        new=lambda command_name: command_name == "npm",
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "_stream_checked",
        new=lambda *args, **kwargs: None,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "strongclaw_qmd_install_dir",
        new=lambda **_: qmd_install_prefix,
    )
    test_context.patch.patch_object(
        strongclaw_bootstrap,
        "run_command",
        new=lambda *args, **kwargs: SimpleNamespace(ok=True),
    )

    wrapper_path = strongclaw_bootstrap.install_qmd_asset(home_dir=tmp_path)

    assert wrapper_path.read_text(encoding="utf-8").endswith(
        f'exec nodejs "{qmd_dist_entry}" "$@"\n'
    )
