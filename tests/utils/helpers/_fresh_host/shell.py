"""Command execution and environment helpers for fresh-host phases."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import context_path, log, repo_root


def phase_env(context: FreshHostContext) -> dict[str, str]:
    """Build the execution environment for one scenario phase."""
    env = dict(os.environ)
    path_prefix = f"{context.app_home}/.local/bin"
    env.update(
        {
            "HOME": context.app_home,
            "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
            "OPENCLAW_MODEL_SETUP_MODE": "skip",
            "PYTHONPATH": "src",
            "XDG_CONFIG_HOME": f"{context.app_home}/.config",
        }
    )
    if context.platform == "linux":
        if context.xdg_runtime_dir is None:
            raise FreshHostError("Linux scenarios require xdg_runtime_dir in context.")
        env["XDG_RUNTIME_DIR"] = context.xdg_runtime_dir
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
        return env

    env["OPENCLAW_MDNS_HOSTNAME"] = "openclaw-ci"
    env["STRONGCLAW_LAUNCHD_SIDECARS_TIMEOUT_SECONDS"] = "2700"
    env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"] = (
        f"{context.app_home}/.openclaw/repo-local-compose"
    )
    env["PATH"] = (
        f"{context.app_home}/.config/varlock/bin:{path_prefix}"
        f":/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:{env.get('PATH', '')}"
    )
    if context.compose_variant is not None:
        env["STRONGCLAW_COMPOSE_VARIANT"] = context.compose_variant
    return env


def repo_paths(context: FreshHostContext) -> tuple[Path, Path]:
    """Return the repo root and app home paths."""
    return repo_root(context.repo_root), context_path(context.app_home)


def system_clawops_command(*arguments: str) -> list[str]:
    """Return the bootstrap-time clawops command."""
    return [sys.executable, "-m", "clawops", *arguments]


def venv_clawops_command(context: FreshHostContext, *arguments: str) -> list[str]:
    """Return the managed-environment clawops command."""
    # Preserve the venv entrypoint path instead of resolving the symlink target.
    return [
        str(repo_root(context.repo_root) / ".venv" / "bin" / "python"),
        "-m",
        "clawops",
        *arguments,
    ]


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    check: bool = True,
) -> None:
    """Run one inherited subprocess command."""
    log("Running: " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        timeout=timeout_seconds,
        text=True,
    )
    if check and completed.returncode != 0:
        raise FreshHostError(
            f"Command failed with exit {completed.returncode}: {' '.join(command)}"
        )


def ensure_dir(path: Path, *, mode: int | None = None) -> None:
    """Create one directory and optionally apply mode bits."""
    path.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        path.chmod(mode)


def ensure_private_dir(path: Path) -> None:
    """Create one user-private directory."""
    ensure_dir(path, mode=stat.S_IRWXU)


def verify_file_exists(path: Path) -> None:
    """Raise when the requested file is missing."""
    if not path.is_file():
        raise FreshHostError(f"Expected file is missing: {path}")


def wait_for_docker_backend(
    *,
    cwd: Path,
    env: dict[str, str],
    max_attempts: int = 5,
    poll_seconds: int = 2,
    probe_timeout_seconds: int = 15,
) -> None:
    """Wait until the local Docker backend is reachable."""
    for attempt in range(1, max_attempts + 1):
        try:
            completed = subprocess.run(
                ["docker", "info"],
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=probe_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            completed = None
        if completed is not None and completed.returncode == 0:
            return
        if attempt < max_attempts:
            log(f"Docker backend not ready yet (attempt {attempt}/{max_attempts}); retrying.")
            time.sleep(poll_seconds)
    raise FreshHostError("Docker backend is not reachable from this shell.")


def best_effort(command: list[str], *, cwd: Path, env: dict[str, str]) -> str | None:
    """Run one best-effort command and return a warning on failure."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{' '.join(command)} failed: {exc}"
    if completed.returncode == 0:
        return None
    output = completed.stderr.strip() or completed.stdout.strip() or "command failed"
    return f"{' '.join(command)} failed: {output}"


def capture_to_file(
    command: list[str], *, output_path: Path, cwd: Path, env: dict[str, str]
) -> str | None:
    """Run one best-effort command and capture stdout/stderr to a file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        output_path.write_text(f"{exc}\n", encoding="utf-8")
        return f"{' '.join(command)} failed: {exc}"
    output_path.write_text(
        "\n".join(chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk)
        + "\n",
        encoding="utf-8",
    )
    if completed.returncode == 0:
        return None
    return f"{' '.join(command)} exited with {completed.returncode}"
