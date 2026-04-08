"""macOS phase implementations for fresh-host CI."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
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
    verify_sidecar_services_running,
    wait_for_docker_backend,
)
from tests.utils.helpers._fresh_host.storage import log

HOSTED_MACOS_SIDECAR_STARTUP_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class _MacosCleanupResult:
    """Structured cleanup execution result."""

    command: list[str] | None
    notes: list[str]


def _managed_launchd_labels(context: FreshHostContext) -> tuple[str, ...]:
    """Return the launchd labels managed by the scenario."""
    if not context.activate_services:
        return ()
    labels = ["ai.openclaw.gateway"]
    if context.exercise_sidecars:
        labels.append("ai.openclaw.sidecars")
    if context.exercise_browser_lab:
        labels.append("ai.openclaw.browserlab")
    return tuple(labels)


def _managed_host_components(context: FreshHostContext) -> tuple[str, ...]:
    """Return the host-managed stack components for the scenario."""
    if not context.activate_services:
        return ()
    components: list[str] = []
    if context.exercise_sidecars:
        components.append("sidecars")
    if context.exercise_browser_lab:
        components.append("browser-lab")
    return tuple(components)


def _repo_local_components(context: FreshHostContext) -> tuple[str, ...]:
    """Return the repo-local stack components for the scenario."""
    components: list[str] = []
    if context.exercise_sidecars:
        components.append("sidecars")
    if context.exercise_browser_lab:
        components.append("browser-lab")
    return tuple(components)


def _launchd_label_for_component(component: str) -> str:
    """Return the launchd label that owns one component."""
    return "ai.openclaw.browserlab" if component == "browser-lab" else f"ai.openclaw.{component}"


def _launchd_service_is_loaded(
    *,
    cwd: Path,
    env: dict[str, str],
    domain: str,
    label: str,
) -> bool:
    """Return whether the requested launchd label is currently loaded."""
    try:
        completed = subprocess.run(
            ["launchctl", "print", f"{domain}/{label}"],
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FreshHostError(f"launchctl print {domain}/{label} failed: {exc}") from exc
    return completed.returncode == 0


def _run_actionable_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Run one teardown command and raise on actionable failure."""
    warning = best_effort(command, cwd=cwd, env=env)
    if warning is not None:
        raise FreshHostError(warning)


def _run_macos_teardown(
    context: FreshHostContext,
    *,
    include_repo_local_state: bool,
) -> _MacosCleanupResult:
    """Execute one scenario-aware macOS teardown plan."""
    repo_root, home_dir = repo_paths(context)
    env = phase_env(context)
    if include_repo_local_state:
        ensure_private_dir(home_dir / ".xdg-runtime")
        env["XDG_RUNTIME_DIR"] = str(home_dir / ".xdg-runtime")
    domain = f"gui/{os.getuid()}"
    launch_agents = home_dir / "Library" / "LaunchAgents"
    notes: list[str] = []
    active_labels: set[str] = set()
    last_command: list[str] | None = None
    venv_entrypoint = repo_root / ".venv" / "bin" / "python"
    skip_venv_teardown = include_repo_local_state and not venv_entrypoint.is_file()
    if skip_venv_teardown:
        note = (
            "Skipping clawops teardown commands: managed venv entrypoint is missing "
            f"({venv_entrypoint})."
        )
        notes.append(note)
        log(note)

    for label in _managed_launchd_labels(context):
        if not _launchd_service_is_loaded(cwd=repo_root, env=env, domain=domain, label=label):
            note = f"Skipping launchctl bootout for {label}: service is not loaded."
            notes.append(note)
            log(note)
            continue
        command = ["launchctl", "bootout", domain, str(launch_agents / f"{label}.plist")]
        _run_actionable_command(command, cwd=repo_root, env=env)
        active_labels.add(label)
        last_command = command

    for component in _managed_host_components(context):
        if _launchd_label_for_component(component) not in active_labels:
            continue
        if skip_venv_teardown:
            continue
        command = venv_clawops_command(context, "ops", "--asset-root", ".", component, "down")
        _run_actionable_command(command, cwd=repo_root, env=env)
        last_command = command

    if include_repo_local_state:
        if skip_venv_teardown:
            return _MacosCleanupResult(command=last_command, notes=notes)
        for component in _repo_local_components(context):
            command = venv_clawops_command(
                context,
                "ops",
                "--asset-root",
                ".",
                component,
                "down",
                "--repo-local-state",
            )
            _run_actionable_command(command, cwd=repo_root, env=env)
            last_command = command

    return _MacosCleanupResult(command=last_command, notes=notes)


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
        "--asset-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        context.profile,
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


def _run_repo_local_cycle(
    context: FreshHostContext, component: str, *, bring_down: bool = True
) -> list[str]:
    """Run one repo-local up/down cycle for a component.

    When *bring_down* is False the services are left running after verification
    so that a subsequent phase can reuse the already-healthy stack without paying
    another startup and health-check round.
    """
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    compose_file = compose_file_for_component(
        context, "browser-lab" if component == "browser-lab" else "sidecars"
    )
    wait_for_docker_backend(cwd=repo_root, env=env)
    up_command = venv_clawops_command(
        context, "ops", "--asset-root", ".", component, "up", "--repo-local-state"
    )
    down_command = venv_clawops_command(
        context,
        "ops",
        "--asset-root",
        ".",
        component,
        "down",
        "--repo-local-state",
    )
    run_command(up_command, cwd=repo_root, env=env)
    if component == "sidecars":
        verify_sidecar_services_running(
            compose_file,
            cwd=repo_root / "platform" / "compose",
            env=env,
            timeout_seconds=HOSTED_MACOS_SIDECAR_STARTUP_TIMEOUT_SECONDS,
            repo_root_path=repo_root,
            repo_local_state=True,
        )
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
    if bring_down:
        run_command(down_command, cwd=repo_root, env=env)
        return down_command
    return up_command


def _venv_python(context: FreshHostContext) -> str:
    """Return the managed-environment Python executable for one scenario."""
    return venv_clawops_command(context)[0]


def exercise_macos_sidecars(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local sidecars.

    When *exercise-channels-runtime* is also scheduled the sidecars stack is left
    running after verification so the next phase can reuse it without a redundant
    up/down cycle.
    """
    bring_down = "exercise-channels-runtime" not in context.phase_names
    return _run_repo_local_cycle(context, "sidecars", bring_down=bring_down)


def exercise_macos_browser_lab(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local browser-lab."""
    return _run_repo_local_cycle(context, "browser-lab")


def exercise_macos_channels_runtime(context: FreshHostContext) -> list[str]:
    """Exercise channels acceptance while sidecars are running repo-locally.

    When *exercise-sidecars* is also scheduled the sidecars stack is already running
    from that phase, so this function skips the redundant up/verify cycle and proceeds
    directly to channels exercises before tearing the stack down.
    """
    repo_root, _ = repo_paths(context)
    env = phase_env(context)
    env.setdefault("STRONGCLAW_CHANNELS_RUNTIME_TELEGRAM_BOT_TOKEN", "fresh-host-smoke-token")
    wait_for_docker_backend(cwd=repo_root, env=env)

    sidecars_already_up = "exercise-sidecars" in context.phase_names
    if not sidecars_already_up:
        compose_file = compose_file_for_component(context, "sidecars")
        up_command = venv_clawops_command(
            context, "ops", "--asset-root", ".", "sidecars", "up", "--repo-local-state"
        )
        run_command(up_command, cwd=repo_root, env=env)
        verify_sidecar_services_running(
            compose_file,
            cwd=repo_root / "platform" / "compose",
            env=env,
            timeout_seconds=HOSTED_MACOS_SIDECAR_STARTUP_TIMEOUT_SECONDS,
            repo_root_path=repo_root,
            repo_local_state=True,
        )

    channels_verify_command = venv_clawops_command(
        context, "verify-platform", "channels", "--asset-root", "."
    )
    channels_contract_command = [
        _venv_python(context),
        "./tests/scripts/security_workflow.py",
        "verify-channels-contract",
        "--repo-root",
        ".",
    ]
    channels_runtime_smoke_command = [
        _venv_python(context),
        "./tests/scripts/security_workflow.py",
        "run-channels-runtime-smoke",
        "--repo-root",
        ".",
        "--artifact-path",
        str(context_path(context.report_dir) / "channels-runtime-smoke.json"),
    ]
    down_command = venv_clawops_command(
        context,
        "ops",
        "--asset-root",
        ".",
        "sidecars",
        "down",
        "--repo-local-state",
    )
    run_command(channels_verify_command, cwd=repo_root, env=env)
    run_command(channels_contract_command, cwd=repo_root, env=env)
    run_command(channels_runtime_smoke_command, cwd=repo_root, env=env)
    run_command(down_command, cwd=repo_root, env=env)
    return down_command


def exercise_macos_recovery_smoke(context: FreshHostContext) -> list[str]:
    """Exercise backup/verify/restore smoke in the macOS fresh-host lane."""
    repo_root, _ = repo_paths(context)
    command = [
        _venv_python(context),
        "./tests/scripts/security_workflow.py",
        "run-recovery-smoke",
        "--tmp-root",
        context.tmp_root,
        "--require-openclaw-cli",
    ]
    run_command(command, cwd=repo_root, env=phase_env(context))
    return command


def deactivate_macos_host_services(context: FreshHostContext) -> list[str] | None:
    """Stop launchd-managed macOS services before repo-local exercises."""
    return _run_macos_teardown(context, include_repo_local_state=False).command


def cleanup_macos(context: FreshHostContext) -> _MacosCleanupResult:
    """Clean up macOS launchd and compose state for the active scenario."""
    return _run_macos_teardown(context, include_repo_local_state=True)
