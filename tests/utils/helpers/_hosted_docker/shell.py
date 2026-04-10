"""Shell execution helpers for hosted Docker CI."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._hosted_docker.io import log


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command with optional captured output."""
    log("Running: " + " ".join(command))
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=timeout_seconds,
    )


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command and raise on non-zero exit."""
    completed = run_command(
        command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        capture_output=capture_output,
    )
    if completed.returncode != 0:
        output = (
            (completed.stderr or "").strip() or (completed.stdout or "").strip() or "command failed"
        )
        raise FreshHostError(f"{' '.join(command)} failed: {output}")
    return completed


def wait_for_docker_ready(
    *,
    cwd: Path,
    env: dict[str, str],
    max_attempts: int = 60,
    poll_seconds: int = 2,
    probe_timeout_seconds: int = 30,
) -> None:
    """Wait until the Docker daemon answers successfully."""
    docker_host = env.get("DOCKER_HOST") or os.environ.get("DOCKER_HOST") or "(not set)"
    log(
        f"Waiting for Docker — DOCKER_HOST={docker_host} (max {max_attempts} attempts, "
        f"{poll_seconds}s between probes, {probe_timeout_seconds}s probe timeout)."
    )
    for attempt in range(1, max_attempts + 1):
        if attempt == 1 or attempt % 10 == 0:
            socket_path = docker_host.removeprefix("unix://")
            socket_exists = (
                Path(socket_path).exists() if docker_host.startswith("unix://") else None
            )
            log(
                f"[attempt {attempt}/{max_attempts}] socket {socket_path}: "
                f"{'EXISTS' if socket_exists else 'NOT FOUND'}."
            )
        try:
            ready = run_command(
                ["docker", "info"],
                cwd=cwd,
                env=env,
                timeout_seconds=probe_timeout_seconds,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            log(
                f"Docker readiness probe timed out after "
                f"{probe_timeout_seconds}s (attempt {attempt}/{max_attempts})."
            )
        except FileNotFoundError:
            pass  # docker binary not yet in PATH; keep retrying
        else:
            if ready.returncode == 0:
                log(f"Docker ready after {attempt} attempt(s).")
                return
            if attempt == 1 or attempt % 10 == 0:
                stderr_snippet = (ready.stderr or "").strip()[:400]
                log(
                    f"[attempt {attempt}/{max_attempts}] docker info rc={ready.returncode}: "
                    f"{stderr_snippet or '(no output)'}"
                )
        if attempt < max_attempts:
            time.sleep(poll_seconds)
    raise FreshHostError("docker did not become ready")


def sysctl_int(name: str) -> int | None:
    """Return one integer sysctl value when available."""
    try:
        completed = subprocess.run(
            ["sysctl", "-n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    raw_value = completed.stdout.strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def macos_env() -> dict[str, str]:
    """Return the base environment for hosted macOS tooling."""
    env = dict(os.environ)
    env.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
    env.setdefault("HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK", "1")
    env.setdefault("HOMEBREW_NO_INSTALL_CLEANUP", "1")
    env.setdefault("HOMEBREW_NO_INSTALL_UPGRADE", "1")
    return env
