"""Command execution and environment helpers for fresh-host phases."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Collection
from pathlib import Path
from typing import Literal, cast

from clawops.strongclaw_compose import compose_project_name
from clawops.strongclaw_runtime import (
    expand_user_path,
    load_env_assignments,
    resolve_openclaw_config_path,
    resolve_repo_local_compose_state_dir,
    varlock_local_env_file,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import context_path, log, repo_root

SIDECAR_EXPECTED_SERVICES: tuple[str, ...] = (
    "postgres",
    "litellm",
    "otel-collector",
    "qdrant",
)
SIDECAR_HEALTHY_SERVICES: tuple[str, ...] = ("postgres", "litellm", "qdrant")


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


def compose_file_for_component(
    context: FreshHostContext, component: Literal["sidecars", "browser-lab"]
) -> Path:
    """Return the compose file used by one repo-local fresh-host exercise."""
    needle = "browser-lab" if component == "browser-lab" else "aux-stack"
    for raw_path in context.compose_files:
        compose_path = context_path(raw_path)
        if needle in compose_path.name:
            return compose_path
    raise FreshHostError(f"Missing compose file for fresh-host component '{component}'.")


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


def _coerce_compose_ps_entries(stdout: str) -> list[dict[str, object]]:
    """Parse ``docker compose ps --format json`` output."""
    text = stdout.strip()
    if not text:
        raise FreshHostError("docker compose ps returned no service data.")

    def _coerce_entry(payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise FreshHostError("docker compose ps returned a non-object entry.")
        return {str(key): value for key, value in cast(dict[object, object], payload).items()}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        entries: list[dict[str, object]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed_line = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise FreshHostError("docker compose ps returned invalid JSON.") from exc
            entries.append(_coerce_entry(parsed_line))
        return entries

    if isinstance(payload, list):
        return [_coerce_entry(entry) for entry in cast(list[object], payload)]
    return [_coerce_entry(payload)]


def _string_field(entry: dict[str, object], *names: str) -> str | None:
    """Return the first matching string field from one compose entry."""
    for name in names:
        value = entry.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _compose_probe_env(
    base_env: dict[str, str],
    *,
    repo_root_path: Path,
    compose_name: str,
    repo_local_state: bool,
) -> dict[str, str]:
    """Build the compose env used by the runtime probe."""
    probe_env = dict(base_env)
    home_dir = Path(probe_env.get("HOME", Path.home().as_posix())).expanduser().resolve()
    local_env = load_env_assignments(varlock_local_env_file(repo_root_path))
    for key, value in local_env.items():
        if value and not probe_env.get(key, "").strip():
            probe_env[key] = value
    openclaw_state_dir = expand_user_path(
        local_env.get("OPENCLAW_STATE_DIR", "~/.openclaw"),
        home_dir=home_dir,
    )
    explicit_state_dir = probe_env.get("STRONGCLAW_COMPOSE_STATE_DIR", "").strip()
    repo_local_override = probe_env.get("STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR", "").strip()
    if explicit_state_dir:
        state_dir = Path(explicit_state_dir).expanduser().resolve()
    elif repo_local_state:
        if repo_local_override:
            state_dir = expand_user_path(repo_local_override, home_dir=home_dir)
        else:
            state_dir = resolve_repo_local_compose_state_dir(repo_root_path)
    else:
        state_dir = openclaw_state_dir / "compose"
    state_dir.mkdir(parents=True, exist_ok=True)
    probe_env["OPENCLAW_STATE_DIR"] = str(openclaw_state_dir)
    probe_env["STRONGCLAW_COMPOSE_STATE_DIR"] = str(state_dir)
    if repo_local_override:
        probe_env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"] = str(state_dir)
    openclaw_config = probe_env.get("OPENCLAW_CONFIG", "").strip()
    if openclaw_config:
        probe_env["OPENCLAW_CONFIG"] = str(expand_user_path(openclaw_config, home_dir=home_dir))
    else:
        probe_env["OPENCLAW_CONFIG"] = str(
            resolve_openclaw_config_path(repo_root_path, home_dir=home_dir)
        )
    project_name = compose_project_name(
        compose_name=compose_name,
        state_dir=state_dir,
        repo_local_state=repo_local_state,
        environ=probe_env,
    )
    if project_name is not None:
        probe_env["COMPOSE_PROJECT_NAME"] = project_name
    return probe_env


def compose_probe_env(
    context: FreshHostContext,
    *,
    compose_file: Path,
    repo_local_state: bool,
) -> dict[str, str]:
    """Build the compose env for one context-aware probe or diagnostic command."""
    return _compose_probe_env(
        phase_env(context),
        repo_root_path=repo_root(context.repo_root),
        compose_name=compose_file.name,
        repo_local_state=repo_local_state,
    )


def verify_compose_services_running(
    compose_file: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    expected_services: tuple[str, ...],
    healthy_services: Collection[str] = (),
    timeout_seconds: int = 120,
    repo_root_path: Path | None = None,
    repo_local_state: bool = False,
) -> None:
    """Assert that the expected compose services become running within the timeout window."""
    deadline = time.monotonic() + timeout_seconds
    last_error: FreshHostError | None = None
    probe_env = (
        _compose_probe_env(
            env,
            repo_root_path=repo_root_path,
            compose_name=compose_file.name,
            repo_local_state=repo_local_state,
        )
        if repo_root_path is not None
        else env
    )

    while True:
        completed = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"],
            cwd=cwd,
            env=probe_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if completed.returncode == 0:
            try:
                entries = _coerce_compose_ps_entries(completed.stdout)
            except FreshHostError as exc:
                last_error = exc
            else:
                services: dict[str, dict[str, object]] = {}
                for entry in entries:
                    service_name = _string_field(entry, "Service", "service")
                    if service_name is not None:
                        services[service_name] = entry

                missing_services = sorted(
                    service_name
                    for service_name in expected_services
                    if service_name not in services
                )
                if not missing_services:
                    for service_name in expected_services:
                        state = (
                            _string_field(services[service_name], "State", "state") or ""
                        ).lower()
                        if state != "running":
                            last_error = FreshHostError(
                                "docker compose ps reports service "
                                f"'{service_name}' in state '{state or 'unknown'}'."
                            )
                            break
                        if service_name in healthy_services:
                            health = (
                                _string_field(services[service_name], "Health", "health") or ""
                            ).lower()
                            if health != "healthy":
                                last_error = FreshHostError(
                                    "docker compose ps reports service "
                                    f"'{service_name}' health '{health or 'unknown'}'."
                                )
                                break
                    else:
                        return
                else:
                    last_error = FreshHostError(
                        "docker compose ps is missing expected services: "
                        + ", ".join(missing_services)
                    )
        else:
            detail = (
                completed.stderr.strip() or completed.stdout.strip() or "docker compose ps failed"
            )
            last_error = FreshHostError(detail)

        if time.monotonic() >= deadline:
            break
        time.sleep(2.0)

    assert last_error is not None
    raise last_error
    raise FreshHostError("Timed out waiting for docker compose services to start.")


def verify_sidecar_services_running(
    compose_file: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 90,
    repo_root_path: Path | None = None,
    repo_local_state: bool = False,
) -> None:
    """Assert that the repo-local sidecar stack reaches a healthy running state."""
    verify_compose_services_running(
        compose_file,
        cwd=cwd,
        env=env,
        expected_services=SIDECAR_EXPECTED_SERVICES,
        healthy_services=SIDECAR_HEALTHY_SERVICES,
        timeout_seconds=timeout_seconds,
        repo_root_path=repo_root_path,
        repo_local_state=repo_local_state,
    )


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
