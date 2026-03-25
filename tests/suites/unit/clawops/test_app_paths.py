"""Tests for shared StrongClaw app data and state path helpers."""

from __future__ import annotations

import pathlib

from clawops.app_paths import (
    scoped_state_dir,
    strongclaw_compose_state_dir,
    strongclaw_data_dir,
    strongclaw_log_dir,
    strongclaw_lossless_claw_dir,
    strongclaw_qmd_install_dir,
    strongclaw_runs_dir,
    strongclaw_state_dir,
)


def test_linux_defaults_follow_xdg_conventions(tmp_path: pathlib.Path) -> None:
    home_dir = tmp_path / "home"
    env = {
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
    }

    assert strongclaw_data_dir(home_dir=home_dir, environ=env, os_name="Linux") == (
        tmp_path / "xdg-data" / "strongclaw"
    )
    assert strongclaw_state_dir(home_dir=home_dir, environ=env, os_name="Linux") == (
        tmp_path / "xdg-state" / "strongclaw"
    )
    assert strongclaw_log_dir(home_dir=home_dir, environ=env, os_name="Linux") == (
        tmp_path / "xdg-state" / "strongclaw" / "logs"
    )
    assert strongclaw_runs_dir(home_dir=home_dir, environ=env, os_name="Linux") == (
        tmp_path / "xdg-state" / "strongclaw" / "runs"
    )
    assert strongclaw_compose_state_dir(home_dir=home_dir, environ=env, os_name="Linux") == (
        tmp_path / "xdg-state" / "strongclaw" / "compose"
    )


def test_macos_defaults_use_application_support_and_logs(tmp_path: pathlib.Path) -> None:
    home_dir = tmp_path / "home"

    assert strongclaw_data_dir(home_dir=home_dir, os_name="Darwin") == (
        home_dir / "Library" / "Application Support" / "StrongClaw"
    )
    assert strongclaw_state_dir(home_dir=home_dir, os_name="Darwin") == (
        home_dir / "Library" / "Application Support" / "StrongClaw" / "state"
    )
    assert strongclaw_log_dir(home_dir=home_dir, os_name="Darwin") == (
        home_dir / "Library" / "Logs" / "StrongClaw"
    )


def test_override_paths_apply_to_lossless_and_qmd_installs(tmp_path: pathlib.Path) -> None:
    env = {
        "STRONGCLAW_DATA_DIR": str(tmp_path / "data-root"),
        "STRONGCLAW_STATE_DIR": str(tmp_path / "state-root"),
    }

    assert strongclaw_lossless_claw_dir(environ=env) == (
        tmp_path / "data-root" / "plugins" / "lossless-claw"
    )
    assert strongclaw_qmd_install_dir(environ=env) == tmp_path / "data-root" / "qmd"
    scoped_dir = scoped_state_dir(tmp_path / "repo", category="acp", environ=env)
    assert scoped_dir.parent.parent == tmp_path / "state-root" / "workspaces"
    assert scoped_dir.parent.name.startswith("repo-")
    assert scoped_dir.name == "acp"
