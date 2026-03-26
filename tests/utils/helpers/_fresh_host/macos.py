"""macOS phase implementations for fresh-host CI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.utils.helpers._fresh_host.linux import run_clawops_bootstrap
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.shell import (
    best_effort,
    compose_file_for_component,
    context_path,
    ensure_dir,
    ensure_private_dir,
    phase_env,
    repo_paths,
    run_command,
    system_clawops_command,
    venv_clawops_command,
    verify_compose_services_running,
    verify_file_exists,
    wait_for_docker_backend,
)


def normalize_macos_machine_name(_: FreshHostContext) -> list[str]:
    """Normalize the hosted macOS machine name."""
    commands = [
        ["sudo", "scutil", "--set", "ComputerName", "openclaw-ci"],
        ["sudo", "scutil", "--set", "LocalHostName", "openclaw-ci"],
        ["sudo", "scutil", "--set", "HostName", "openclaw-ci"],
        ["scutil", "--get", "ComputerName"],
        ["scutil", "--get", "LocalHostName"],
    ]
    for command in commands:
        run_command(command, cwd=Path.cwd(), env=dict(os.environ))
    return commands[-1]


def macos_bootstrap(context: FreshHostContext) -> list[str]:
    """Bootstrap the macOS host."""
    repo_root, app_home = repo_paths(context)
    ensure_dir(app_home)
    return run_clawops_bootstrap(system_clawops_command(), repo_root, phase_env(context), context)


def macos_setup(context: FreshHostContext) -> list[str]:
    """Run the macOS setup flow."""
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    ensure_dir(context_path(env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"]))
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
    )
    if not context.activate_services:
        command.append("--no-activate-services")
    run_command(command, cwd=repo_root, env=env)
    return command


def verify_macos_launchd(context: FreshHostContext) -> None:
    """Verify macOS launchd outputs."""
    home_dir = context_path(context.app_home)
    launch_agents = home_dir / "Library" / "LaunchAgents"
    for filename in (
        "ai.openclaw.gateway.plist",
        "ai.openclaw.sidecars.plist",
        "ai.openclaw.browserlab.plist",
    ):
        verify_file_exists(launch_agents / filename)
    if not context.verify_launchd:
        return
    env = phase_env(context)
    domain = f"gui/{os.getuid()}"
    for target, needle in (
        (f"{domain}/ai.openclaw.gateway", "state = running"),
        (f"{domain}/ai.openclaw.sidecars", "last exit code = 0"),
    ):
        result = subprocess.run(
            ["launchctl", "print", target],
            cwd=context.repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip() or "launchctl failed"
            raise FreshHostError(output)
        if needle not in result.stdout:
            raise FreshHostError(f"launchd state check failed for {target}")


def _run_repo_local_cycle(context: FreshHostContext, component: str) -> list[str]:
    """Run one repo-local up/down cycle for a component."""
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    compose_file = compose_file_for_component(
        context, "browser-lab" if component == "browser-lab" else "sidecars"
    )
    wait_for_docker_backend(cwd=repo_root, env=env)
    up_command = venv_clawops_command(
        context, "ops", "--repo-root", ".", component, "up", "--repo-local-state"
    )
    down_command = venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        component,
        "down",
        "--repo-local-state",
    )
    run_command(up_command, cwd=repo_root, env=env)
    if component == "sidecars":
        verify_command = venv_clawops_command(
            context,
            "verify-platform",
            "--repo-root",
            ".",
            "sidecars",
            "--compose-file",
            str(compose_file),
        )
        run_command(verify_command, cwd=repo_root, env=env)
    else:
        verify_compose_services_running(
            compose_file,
            cwd=repo_root / "platform" / "compose",
            env=env,
            expected_services=("browserlab-proxy", "browserlab-playwright"),
            timeout_seconds=20,
            repo_root_path=repo_root,
            repo_local_state=True,
        )
    run_command(down_command, cwd=repo_root, env=env)
    return down_command


def exercise_macos_sidecars(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local sidecars."""
    return _run_repo_local_cycle(context, "sidecars")


def exercise_macos_browser_lab(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local browser-lab."""
    return _run_repo_local_cycle(context, "browser-lab")


def deactivate_macos_host_services(context: FreshHostContext) -> list[str]:
    """Stop launchd-managed macOS services before repo-local exercises."""
    repo_root, home_dir = repo_paths(context)
    env = phase_env(context)
    domain = f"gui/{os.getuid()}"
    launch_agents = home_dir / "Library" / "LaunchAgents"
    commands = [
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.gateway.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.sidecars.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.browserlab.plist")],
        venv_clawops_command(context, "ops", "--repo-root", ".", "sidecars", "down"),
        venv_clawops_command(context, "ops", "--repo-root", ".", "browser-lab", "down"),
    ]
    for command in commands:
        warning = best_effort(command, cwd=repo_root, env=env)
        if warning is not None:
            from tests.utils.helpers._fresh_host.storage import log

            log(warning)
    return commands[-1]


def cleanup_macos(context: FreshHostContext) -> list[str]:
    """Best-effort cleanup for macOS launchd and compose state."""
    repo_root, home_dir = repo_paths(context)
    env = phase_env(context)
    ensure_private_dir(home_dir / ".xdg-runtime")
    env["XDG_RUNTIME_DIR"] = str(home_dir / ".xdg-runtime")
    domain = f"gui/{os.getuid()}"
    launch_agents = home_dir / "Library" / "LaunchAgents"
    commands = [
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.gateway.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.sidecars.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.browserlab.plist")],
        venv_clawops_command(context, "ops", "--repo-root", ".", "sidecars", "down"),
        venv_clawops_command(context, "ops", "--repo-root", ".", "browser-lab", "down"),
        venv_clawops_command(
            context, "ops", "--repo-root", ".", "sidecars", "down", "--repo-local-state"
        ),
        venv_clawops_command(
            context, "ops", "--repo-root", ".", "browser-lab", "down", "--repo-local-state"
        ),
    ]
    for command in commands:
        warning = best_effort(command, cwd=repo_root, env=env)
        if warning is not None:
            from tests.utils.helpers._fresh_host.storage import log

            log(warning)
    return commands[-1]
