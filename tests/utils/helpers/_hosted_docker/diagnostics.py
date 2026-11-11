"""Diagnostics collection for hosted Docker CI.

Every collection runs inside one deadline. Commands run cheapest first, so a hung Docker
daemon can only cost the evidence collected after it; any command the deadline leaves no
time for still gets an output file that says it was skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.budget import (
    SYSTEM_CLOCK,
    BudgetExhaustedError,
    Clock,
    PhaseDeadline,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext
from tests.utils.helpers._fresh_host.shell import compose_probe_env, phase_env
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.shell import run_command, sysctl_int

DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS: Final[int] = 120
SYSCTL_TIMEOUT_SECONDS: Final[int] = 10
# Wall budget for the standalone `collect-diagnostics` observer step.
DIAGNOSTICS_COLLECTION_CAP_SECONDS: Final[int] = 120
_ORB_START_LOG: Final[Path] = Path("/tmp/orb-start.log")


def _socket_state(env: dict[str, str]) -> str:
    """Return diagnostic state for Docker socket paths."""
    docker_host = env.get("DOCKER_HOST") or os.environ.get("DOCKER_HOST", "")
    socket_paths: list[Path] = []
    if docker_host.startswith("unix://"):
        socket_paths.append(Path(docker_host.removeprefix("unix://")))
    canonical_orbstack_socket = Path.home() / ".orbstack" / "run" / "docker.sock"
    if canonical_orbstack_socket not in socket_paths:
        socket_paths.append(canonical_orbstack_socket)

    lines = [f"DOCKER_HOST={docker_host or '(not set)'}"]
    for socket_path in socket_paths:
        lines.append(f"path={socket_path}")
        lines.append(f"exists={socket_path.exists()}")
        lines.append(f"is_socket={socket_path.is_socket()}")
    return "\n".join(lines) + "\n"


def _diagnostic_command_env(
    context: FreshHostContext,
    *,
    command: list[str],
    base_env: dict[str, str],
) -> dict[str, str]:
    """Return the environment for one runtime diagnostic command."""
    if len(command) < 4 or command[:3] != ["docker", "compose", "-f"]:
        return base_env

    command_env = compose_probe_env(
        context,
        compose_file=Path(command[3]),
        repo_local_state=True,
    )
    for key in ("DOCKER_CONFIG", "DOCKER_HOST", "PATH"):
        if key in base_env:
            command_env[key] = base_env[key]
    return command_env


def _diagnostic_commands(context: FreshHostContext, root: Path) -> dict[Path, list[str]]:
    """Return output path -> command, ordered from cheap local probes to slow daemon queries."""
    commands: dict[Path, list[str]] = {
        root / "orb-status.txt": ["orb", "status"],
        root / "orb-version.txt": ["orb", "version"],
        root / "vm-stat.txt": ["vm_stat"],
        root / "disk-usage.txt": ["df", "-h"],
        root / "docker-info.txt": ["docker", "info"],
        root / "docker-context-ls.txt": ["docker", "context", "ls"],
        root / "docker-ps-all.txt": ["docker", "ps", "-a"],
        root / "docker-system-df.txt": ["docker", "system", "df"],
        root / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
    }
    if context.compose_files:
        primary_compose_file = context.compose_files[0]
        commands[root / "compose-ps.txt"] = ["docker", "compose", "-f", primary_compose_file, "ps"]
        commands[root / "compose-logs.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "logs",
            "--no-color",
        ]
    commands[root / "orbstack-unified.log"] = [
        "/usr/bin/log",
        "show",
        "--style",
        "compact",
        "--last",
        "30m",
        "--predicate",
        'process CONTAINS[c] "OrbStack" OR subsystem CONTAINS[c] "orbstack"',
    ]
    return commands


def _capture(
    command: list[str],
    output_path: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    deadline: PhaseDeadline,
    clock: Clock,
) -> None:
    """Run one diagnostic command inside *deadline* and write whatever it produced."""
    try:
        completed = run_command(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=deadline.command_timeout(DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS, clock),
            capture_output=True,
        )
    except BudgetExhaustedError as exc:
        output_path.write_text(f"skipped: {exc}\n", encoding="utf-8")
        return
    except (OSError, subprocess.TimeoutExpired) as exc:
        output_path.write_text(f"{exc}\n", encoding="utf-8")
        return
    output_path.write_text(
        "\n".join(chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk)
        + "\n",
        encoding="utf-8",
    )


def _host_value(name: str, *, deadline: PhaseDeadline, clock: Clock) -> str:
    """Return one sysctl value, or nothing once the deadline is spent."""
    timeout = deadline.clip(SYSCTL_TIMEOUT_SECONDS, clock)
    if timeout <= 0:
        return ""
    return str(sysctl_int(name, timeout_seconds=timeout) or "")


def collect_runtime_diagnostics_for_context(
    context: FreshHostContext,
    diagnostics_dir: Path,
    *,
    deadline: PhaseDeadline,
    env: dict[str, str] | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> None:
    """Collect best-effort runtime diagnostics for one hosted macOS context within *deadline*."""
    if context.platform != "macos":
        return
    resolved_diagnostics_dir = diagnostics_dir.resolve()
    resolved_diagnostics_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(context.repo_root).resolve()
    base_env = dict(env) if env is not None else phase_env(context)
    (resolved_diagnostics_dir / "docker-socket-state.txt").write_text(
        _socket_state(base_env),
        encoding="utf-8",
    )
    if _ORB_START_LOG.is_file():
        try:
            orb_start_content = _ORB_START_LOG.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            orb_start_content = f"failed to read {_ORB_START_LOG}: {exc}\n"
        (resolved_diagnostics_dir / "orb-start.log").write_text(
            orb_start_content,
            encoding="utf-8",
        )
    for output_path, sysctl_name in (
        (resolved_diagnostics_dir / "host-cpu-count.txt", "hw.ncpu"),
        (resolved_diagnostics_dir / "host-memory-bytes.txt", "hw.memsize"),
    ):
        value = _host_value(sysctl_name, deadline=deadline, clock=clock)
        output_path.write_text(f"{value}\n", encoding="utf-8")
    for output_path, command in _diagnostic_commands(context, resolved_diagnostics_dir).items():
        _capture(
            command,
            output_path,
            cwd=repo_root,
            env=_diagnostic_command_env(context, command=command, base_env=base_env),
            deadline=deadline,
            clock=clock,
        )


def collect_runtime_diagnostics(context_path: Path, *, clock: Clock = SYSTEM_CLOCK) -> None:
    """Collect best-effort runtime diagnostics for hosted macOS within the observer cap."""
    context = load_context(context_path)
    deadline = PhaseDeadline.after(DIAGNOSTICS_COLLECTION_CAP_SECONDS, clock=clock)
    diagnostics_dir = Path(context.diagnostics_dir).resolve()
    collect_runtime_diagnostics_for_context(
        context, diagnostics_dir, deadline=deadline, clock=clock
    )
    if context.platform != "macos":
        return
    cache_root = os.environ.get("FRESH_HOST_CACHE_ROOT", "")
    if cache_root:
        _capture(
            ["du", "-sh", cache_root],
            diagnostics_dir / "workflow-cache-usage.txt",
            cwd=Path(context.repo_root).resolve(),
            env=phase_env(context),
            deadline=deadline,
            clock=clock,
        )
