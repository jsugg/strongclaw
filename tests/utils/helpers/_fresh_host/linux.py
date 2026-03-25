"""Linux phase implementations for fresh-host CI."""

from __future__ import annotations

from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostContext
from tests.utils.helpers._fresh_host.shell import (
    ensure_dir,
    ensure_private_dir,
    phase_env,
    repo_paths,
    run_command,
    system_clawops_command,
    venv_clawops_command,
    verify_file_exists,
)
from tests.utils.helpers._fresh_host.storage import context_path


def run_clawops_bootstrap(
    command: list[str], repo_root: Path, env: dict[str, str], context: FreshHostContext
) -> list[str]:
    """Run one bootstrap command."""
    full_command = [
        *command,
        "bootstrap",
        "--repo-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        "openclaw-default",
    ]
    run_command(full_command, cwd=repo_root, env=env)
    return full_command


def linux_bootstrap(context: FreshHostContext) -> list[str]:
    """Bootstrap the Linux host."""
    repo_root, app_home = repo_paths(context)
    env = phase_env(context)
    xdg_runtime_dir = context_path(context.xdg_runtime_dir or "")
    ensure_dir(app_home)
    ensure_private_dir(xdg_runtime_dir)
    return run_clawops_bootstrap(system_clawops_command(), repo_root, env, context)


def linux_setup(context: FreshHostContext) -> list[str]:
    """Run the Linux setup flow."""
    repo_root, _ = repo_paths(context)
    command = venv_clawops_command(
        context,
        "setup",
        "--repo-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        "openclaw-default",
        "--non-interactive",
        "--no-verify",
        "--no-activate-services",
    )
    run_command(command, cwd=repo_root, env=phase_env(context))
    return command


def verify_linux_rendered_units(context: FreshHostContext) -> None:
    """Verify Linux service files were rendered."""
    unit_root = context_path(context.app_home) / ".config" / "systemd" / "user"
    for filename in (
        "openclaw-gateway.service",
        "openclaw-sidecars.service",
        "openclaw-browserlab.service",
    ):
        verify_file_exists(unit_root / filename)


def exercise_linux_sidecars(context: FreshHostContext) -> list[str]:
    """Exercise Linux repo-local sidecars."""
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    up_command = venv_clawops_command(
        context, "ops", "--repo-root", ".", "sidecars", "up", "--repo-local-state"
    )
    down_command = venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "sidecars",
        "down",
        "--repo-local-state",
    )
    run_command(up_command, cwd=repo_root, env=env)
    run_command(down_command, cwd=repo_root, env=env)
    return down_command


def exercise_linux_browser_lab(context: FreshHostContext) -> list[str]:
    """Exercise Linux repo-local browser-lab."""
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    up_command = venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "up",
        "--repo-local-state",
    )
    down_command = venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "down",
        "--repo-local-state",
    )
    run_command(up_command, cwd=repo_root, env=env)
    run_command(down_command, cwd=repo_root, env=env)
    return down_command
