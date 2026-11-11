"""Shell execution helpers for hosted Docker CI."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.budget import (
    MIN_COMMAND_SECONDS,
    SYSTEM_CLOCK,
    Clock,
    PhaseDeadline,
)
from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._hosted_docker.io import log
from tests.utils.helpers._hosted_docker.process import (
    TERMINATE_GRACE_SECONDS,
    terminate_process_group,
)

DOCKER_READY_POLL_SECONDS: Final[int] = 2
DOCKER_READY_PROBE_TIMEOUT_SECONDS: Final[int] = 30


def _drain(process: subprocess.Popen[str]) -> tuple[str | None, str | None]:
    """Collect what a terminated child wrote, without waiting on escaped grandchildren."""
    try:
        return process.communicate(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        return None, None


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float = 3600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one command in its own process group; on timeout, kill the whole group."""
    log("Running: " + " ".join(command))
    argv = list(command)
    pipe = subprocess.PIPE if capture_output else None
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=pipe,
        stderr=pipe,
        text=True,
        start_new_session=True,
    )
    stdout: str | None
    stderr: str | None
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        stdout, stderr = _drain(process)
        raise subprocess.TimeoutExpired(
            argv, timeout_seconds, output=stdout, stderr=stderr
        ) from None
    except BaseException:
        terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float = 3600,
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
    deadline: PhaseDeadline,
    clock: Clock = SYSTEM_CLOCK,
    poll_seconds: float = DOCKER_READY_POLL_SECONDS,
    probe_timeout_seconds: float = DOCKER_READY_PROBE_TIMEOUT_SECONDS,
) -> None:
    """Probe the Docker daemon until it answers or *deadline* passes.

    Each probe and poll gap is clipped to the time left, so the wait ends at the deadline
    however long individual probes hang. The error carries the last probe's output so the
    caller can classify the failure without probing again.
    """
    docker_host = env.get("DOCKER_HOST") or os.environ.get("DOCKER_HOST") or "(not set)"
    log(
        f"Waiting for Docker — DOCKER_HOST={docker_host} (up to "
        f"{deadline.remaining(clock):.0f}s, {poll_seconds}s between probes, "
        f"{probe_timeout_seconds}s probe timeout)."
    )
    attempt = 0
    last_failure = "no probe ran before the deadline"
    while deadline.remaining(clock) >= MIN_COMMAND_SECONDS:
        attempt += 1
        verbose = attempt == 1 or attempt % 10 == 0
        if verbose:
            socket_path = docker_host.removeprefix("unix://")
            socket_exists = (
                Path(socket_path).exists() if docker_host.startswith("unix://") else None
            )
            log(
                f"[probe {attempt}, {deadline.remaining(clock):.0f}s left] socket "
                f"{socket_path}: {'EXISTS' if socket_exists else 'NOT FOUND'}."
            )
        probe_timeout = deadline.clip(probe_timeout_seconds, clock)
        try:
            ready = run_command(
                ["docker", "info"],
                cwd=cwd,
                env=env,
                timeout_seconds=probe_timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            last_failure = f"docker info timed out after {probe_timeout:.0f}s"
            log(f"Docker readiness probe {attempt} timed out after {probe_timeout:.0f}s.")
        except FileNotFoundError:
            last_failure = "docker binary not yet on PATH"
        else:
            if ready.returncode == 0:
                log(f"Docker ready after {attempt} probe(s).")
                return
            last_failure = (
                (ready.stderr or "").strip()[:400]
                or (ready.stdout or "").strip()[:400]
                or f"docker info exited {ready.returncode}"
            )
            if verbose:
                log(f"[probe {attempt}] docker info rc={ready.returncode}: {last_failure}")
        clock.sleep(deadline.clip(poll_seconds, clock))
    raise FreshHostError(
        f"docker did not become ready before the {deadline.limited_by} deadline "
        f"({attempt} probe(s)); last: {last_failure}"
    )


def sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
    """Return one integer sysctl value when available."""
    try:
        completed = subprocess.run(
            ["sysctl", "-n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
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
