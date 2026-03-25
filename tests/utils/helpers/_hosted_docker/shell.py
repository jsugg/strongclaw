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
        output = completed.stderr.strip() or completed.stdout.strip() or "command failed"
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
    for attempt in range(1, max_attempts + 1):
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
                "Docker readiness probe timed out after "
                f"{probe_timeout_seconds}s (attempt {attempt}/{max_attempts})."
            )
        else:
            if ready.returncode == 0:
                return
        if attempt < max_attempts:
            time.sleep(poll_seconds)
    raise FreshHostError("docker did not become ready after starting colima")


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
