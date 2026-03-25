"""Diagnostics collection for hosted Docker CI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.shell import run_command, sysctl_int


def collect_runtime_diagnostics(context_path: Path) -> None:
    """Collect best-effort runtime diagnostics for hosted macOS."""
    context = load_context(context_path)
    if context.platform != "macos":
        return
    diagnostics_dir = Path(context.diagnostics_dir).resolve()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(context.repo_root).resolve()
    env = dict(os.environ)
    commands = {
        diagnostics_dir / "runtime-status.txt": ["colima", "status"],
        diagnostics_dir / "runtime-list.txt": ["colima", "list"],
        diagnostics_dir / "docker-info.txt": ["docker", "info"],
        diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
        diagnostics_dir / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
    }
    if context.compose_files:
        primary_compose_file = context.compose_files[0]
        commands[diagnostics_dir / "compose-ps.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "ps",
        ]
        commands[diagnostics_dir / "compose-logs.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "logs",
            "--no-color",
        ]
    for output_path, command in commands.items():
        try:
            completed = run_command(
                command,
                cwd=repo_root,
                env=env,
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
        diagnostics_dir / "host-cpu-count.txt": str(sysctl_int("hw.ncpu") or ""),
        diagnostics_dir / "host-memory-bytes.txt": str(sysctl_int("hw.memsize") or ""),
    }.items():
        output_path.write_text(f"{content}\n", encoding="utf-8")
    for output_path, raw_target_path in {
        diagnostics_dir / "colima-disk-usage.txt": str(Path.home() / ".colima"),
        diagnostics_dir / "homebrew-cache-usage.txt": os.environ.get("HOMEBREW_CACHE", ""),
        diagnostics_dir / "workflow-cache-usage.txt": os.environ.get("FRESH_HOST_CACHE_ROOT", ""),
    }.items():
        if not raw_target_path:
            continue
        try:
            completed = run_command(
                ["du", "-sh", raw_target_path],
                cwd=repo_root,
                env=env,
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
    colima_logs_dir = Path.home() / ".colima" / "_lima" / "colima"
    if colima_logs_dir.is_dir():
        target_dir = diagnostics_dir / "colima-logs"
        target_dir.mkdir(parents=True, exist_ok=True)
        for log_path in colima_logs_dir.glob("*.log"):
            shutil.copyfile(log_path, target_dir / log_path.name)
