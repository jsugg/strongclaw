"""Diagnostics collection for hosted Docker CI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostContext
from tests.utils.helpers._fresh_host.shell import compose_probe_env, phase_env
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.shell import run_command, sysctl_int


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


def collect_runtime_diagnostics_for_context(
    context: FreshHostContext,
    diagnostics_dir: Path,
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Collect best-effort runtime diagnostics for one hosted macOS context."""
    if context.platform != "macos":
        return
    resolved_diagnostics_dir = diagnostics_dir.resolve()
    resolved_diagnostics_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(context.repo_root).resolve()
    base_env = dict(env) if env is not None else phase_env(context)
    commands = {
        resolved_diagnostics_dir / "docker-info.txt": ["docker", "info"],
        resolved_diagnostics_dir / "docker-context-ls.txt": ["docker", "context", "ls"],
        resolved_diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
        resolved_diagnostics_dir / "docker-ps-all.txt": ["docker", "ps", "-a"],
        resolved_diagnostics_dir
        / "docker-images.jsonl": [
            "docker",
            "images",
            "--format",
            "{{json .}}",
        ],
        resolved_diagnostics_dir / "orb-status.txt": ["orb", "status"],
        resolved_diagnostics_dir / "orb-logs.txt": ["orb", "logs"],
    }
    if context.compose_files:
        primary_compose_file = context.compose_files[0]
        commands[resolved_diagnostics_dir / "compose-ps.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "ps",
        ]
        commands[resolved_diagnostics_dir / "compose-logs.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "logs",
            "--no-color",
        ]
    for output_path, command in commands.items():
        command_env = _diagnostic_command_env(
            context,
            command=command,
            base_env=base_env,
        )
        try:
            completed = run_command(
                command,
                cwd=repo_root,
                env=command_env,
                timeout_seconds=120,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_path.write_text(f"{exc}\n", encoding="utf-8")
            continue
        output_path.write_text(
            "\n".join(
                chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk
            )
            + "\n",
            encoding="utf-8",
        )
    for output_path, content in {
        resolved_diagnostics_dir / "host-cpu-count.txt": str(sysctl_int("hw.ncpu") or ""),
        resolved_diagnostics_dir / "host-memory-bytes.txt": str(sysctl_int("hw.memsize") or ""),
    }.items():
        output_path.write_text(f"{content}\n", encoding="utf-8")
    (resolved_diagnostics_dir / "docker-socket-state.txt").write_text(
        _socket_state(base_env),
        encoding="utf-8",
    )
    orb_start_log = Path("/tmp/orb-start.log")
    if orb_start_log.is_file():
        try:
            orb_start_content = orb_start_log.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            orb_start_content = f"failed to read {orb_start_log}: {exc}\n"
        (resolved_diagnostics_dir / "orb-start.log").write_text(
            orb_start_content,
            encoding="utf-8",
        )


def collect_runtime_diagnostics(context_path: Path) -> None:
    """Collect best-effort runtime diagnostics for hosted macOS."""
    context = load_context(context_path)
    diagnostics_dir = Path(context.diagnostics_dir).resolve()
    collect_runtime_diagnostics_for_context(context, diagnostics_dir)
    if context.platform != "macos":
        return
    repo_root = Path(context.repo_root).resolve()
    env = phase_env(context)
    cache_root = os.environ.get("FRESH_HOST_CACHE_ROOT", "")
    if cache_root:
        output_path = diagnostics_dir / "workflow-cache-usage.txt"
        try:
            completed = run_command(
                ["du", "-sh", cache_root],
                cwd=repo_root,
                env=env,
                timeout_seconds=120,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_path.write_text(f"{exc}\n", encoding="utf-8")
        else:
            output_path.write_text(
                "\n".join(
                    chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk
                )
                + "\n",
                encoding="utf-8",
            )
