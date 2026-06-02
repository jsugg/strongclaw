"""Hosted macOS runtime installation helpers."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context, load_report, write_report
from tests.utils.helpers._hosted_docker.diagnostics import collect_runtime_diagnostics_for_context
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_github_env, write_json
from tests.utils.helpers._hosted_docker.models import (
    RuntimeInstallReport,
    RuntimeRecoveryAttemptReport,
)
from tests.utils.helpers._hosted_docker.shell import (
    macos_env,
    run_checked,
    run_command,
    sysctl_int,
    wait_for_docker_ready,
)

_INITIAL_DOCKER_READY_ATTEMPTS = 300
_POST_RECOVERY_DOCKER_READY_ATTEMPTS = 90
_RECOVERY_BACKOFF_SECONDS = (5, 15)
_RECOVERY_COMMAND_TIMEOUT_SECONDS = 120
_READINESS_PROBE_TIMEOUT_SECONDS = 15
_RECOVERABLE_RUNTIME_REASONS = {
    "docker_not_ready",
    "docker_probe_timeout",
    "docker_socket_eof",
    "orbstack_socket_missing",
}
_RUNTIME_VALIDATION_COMMANDS = (
    ["docker", "version"],
    ["docker", "compose", "version"],
    ["docker", "info"],
)
_ORBSTACK_MOUNTPOINT = Path("/tmp/orbstack_mnt")
_ORB_START_LOG = Path("/tmp/orb-start.log")


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setup_orbstack(*, dmg_path: Path, dmg_url: str, expected_sha256: str) -> None:
    """Install OrbStack and Docker tooling for hosted macOS runtime checks."""
    if sys.platform != "darwin":
        raise FreshHostError("OrbStack setup is only supported on macOS")

    cwd = Path.cwd()
    env = macos_env()
    resolved_dmg_path = dmg_path.resolve()
    if not resolved_dmg_path.is_file():
        run_checked(
            ["curl", "-fsSL", dmg_url, "-o", str(resolved_dmg_path)],
            cwd=cwd,
            env=env,
            timeout_seconds=600,
        )

    actual_sha256 = _sha256(resolved_dmg_path)
    if actual_sha256 != expected_sha256:
        resolved_dmg_path.unlink(missing_ok=True)
        raise FreshHostError(
            f"OrbStack DMG checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )

    attached = False
    try:
        run_checked(
            [
                "hdiutil",
                "attach",
                "-quiet",
                "-nobrowse",
                "-mountpoint",
                str(_ORBSTACK_MOUNTPOINT),
                str(resolved_dmg_path),
            ],
            cwd=cwd,
            env=env,
            timeout_seconds=300,
        )
        attached = True
        run_checked(
            ["cp", "-R", str(_ORBSTACK_MOUNTPOINT / "OrbStack.app"), "/Applications/"],
            cwd=cwd,
            env=env,
            timeout_seconds=300,
        )
    finally:
        if attached:
            run_checked(
                ["hdiutil", "detach", "-quiet", str(_ORBSTACK_MOUNTPOINT)],
                cwd=cwd,
                env=env,
                timeout_seconds=120,
            )

    run_checked(
        [
            "sudo",
            "ln",
            "-sf",
            "/Applications/OrbStack.app/Contents/MacOS/bin/orb",
            "/usr/local/bin/orb",
        ],
        cwd=cwd,
        env=env,
        timeout_seconds=120,
    )
    run_checked(
        ["brew", "install", "--quiet", "docker", "docker-compose"],
        cwd=cwd,
        env=env,
        timeout_seconds=600,
    )
    brew_prefix = run_checked(
        ["brew", "--prefix"],
        cwd=cwd,
        env=env,
        timeout_seconds=120,
        capture_output=True,
    ).stdout.strip()
    cli_plugins_dir = Path.home() / ".docker" / "cli-plugins"
    cli_plugins_dir.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            "ln",
            "-sfn",
            str(Path(brew_prefix) / "bin" / "docker-compose"),
            str(cli_plugins_dir / "docker-compose"),
        ],
        cwd=cwd,
        env=env,
        timeout_seconds=120,
    )
    with _ORB_START_LOG.open("wb") as orb_start_log:
        subprocess.Popen(
            ["orb", "start"],
            cwd=cwd,
            env=env,
            stdout=orb_start_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _docker_socket_path(docker_host: str) -> Path | None:
    """Return the local Unix socket path from DOCKER_HOST when present."""
    if not docker_host.startswith("unix://"):
        return None
    return Path(docker_host.removeprefix("unix://"))


def _classify_failure_text(text: str) -> str | None:
    """Map Docker/OrbStack command output to one structured failure reason."""
    lowered = text.lower()
    if not lowered.strip():
        return None
    if "eof" in lowered:
        return "docker_socket_eof"
    if "timed out" in lowered or "timeout" in lowered:
        return "docker_probe_timeout"
    if "docker did not become ready" in lowered:
        return "docker_not_ready"
    if "cannot connect" in lowered or "connection refused" in lowered:
        return "docker_not_ready"
    if "daemon" in lowered and ("unavailable" in lowered or "not running" in lowered):
        return "docker_not_ready"
    if "no such file or directory" in lowered and "docker" in lowered:
        return "docker_binary_missing"
    return None


def _classify_runtime_failure(*, cwd: Path, env: dict[str, str], exc: BaseException) -> str:
    """Return a structured runtime failure reason."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "docker_probe_timeout"
    if isinstance(exc, FileNotFoundError):
        return "docker_binary_missing"

    exception_reason = _classify_failure_text(str(exc))
    if exception_reason is not None and exception_reason != "docker_not_ready":
        return exception_reason

    docker_host = env.get("DOCKER_HOST") or os.environ.get("DOCKER_HOST", "")
    socket_path = _docker_socket_path(docker_host)
    if socket_path is not None and (not socket_path.exists() or not socket_path.is_socket()):
        return "orbstack_socket_missing"

    try:
        completed = run_command(
            ["docker", "info"],
            cwd=cwd,
            env=env,
            timeout_seconds=_READINESS_PROBE_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        return "docker_probe_timeout"
    except FileNotFoundError:
        return "docker_binary_missing"
    except OSError as probe_exc:
        return _classify_failure_text(str(probe_exc)) or "docker_not_ready"

    if completed.returncode == 0:
        return exception_reason or "runtime_tool_validation_failed"
    probe_text = "\n".join(
        chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk
    )
    return _classify_failure_text(probe_text) or exception_reason or "docker_not_ready"


def _validate_runtime_ready(
    *,
    cwd: Path,
    env: dict[str, str],
    max_attempts: int,
) -> None:
    """Verify Docker readiness and required CLI tooling."""
    wait_for_docker_ready(cwd=cwd, env=env, max_attempts=max_attempts)
    for command in _RUNTIME_VALIDATION_COMMANDS:
        run_checked(command, cwd=cwd, env=env, timeout_seconds=120)


def _write_recovery_command_output(
    *,
    output_path: Path,
    command: list[str],
    completed: subprocess.CompletedProcess[str] | None = None,
    exc: BaseException | None = None,
) -> None:
    """Append one recovery command result to the attempt command artifact."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"$ {' '.join(command)}"]
    if completed is not None:
        lines.append(f"exit_code={completed.returncode}")
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout:
            lines.extend(("stdout:", stdout))
        if stderr:
            lines.extend(("stderr:", stderr))
    if exc is not None:
        lines.append(f"error={exc}")
    with output_path.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n\n")


def _run_recovery_commands(
    *,
    cwd: Path,
    env: dict[str, str],
    trigger_reason: str,
    output_path: Path,
) -> tuple[list[list[str]], int | None, bool]:
    """Run bounded OrbStack recovery commands and capture their output."""
    primary_command = (
        ["orb", "start"]
        if trigger_reason == "orbstack_socket_missing"
        else ["orb", "restart", "docker"]
    )
    commands = [primary_command]
    exit_code: int | None = None
    timed_out = False

    for command in commands:
        try:
            completed = run_command(
                command,
                cwd=cwd,
                env=env,
                timeout_seconds=_RECOVERY_COMMAND_TIMEOUT_SECONDS,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            _write_recovery_command_output(output_path=output_path, command=command, exc=exc)
            exit_code = None
            break
        except OSError as exc:
            _write_recovery_command_output(output_path=output_path, command=command, exc=exc)
            exit_code = None
            break

        exit_code = completed.returncode
        _write_recovery_command_output(
            output_path=output_path,
            command=command,
            completed=completed,
        )
        if completed.returncode == 0 and command != ["orb", "stop"]:
            return commands, exit_code, timed_out
        if command == ["orb", "restart", "docker"]:
            fallback_commands = [["orb", "stop"], ["orb", "start"]]
            commands.extend(fallback_commands)

    return commands, exit_code, timed_out


def _recover_orbstack_runtime(
    *,
    context: FreshHostContext,
    repo_root: Path,
    env: dict[str, str],
    trigger_reason: str,
) -> tuple[bool, list[RuntimeRecoveryAttemptReport], str | None]:
    """Attempt bounded OrbStack recovery and return structured attempt reports."""
    attempts: list[RuntimeRecoveryAttemptReport] = []
    last_readiness_reason: str | None = trigger_reason
    recovery_root = Path(context.diagnostics_dir).resolve() / "runtime-recovery"

    for attempt_number, backoff_seconds in enumerate(_RECOVERY_BACKOFF_SECONDS, start=1):
        attempt_root = recovery_root / f"attempt-{attempt_number:02d}"
        before_dir = attempt_root / "before"
        after_dir = attempt_root / "after"
        output_path = attempt_root / "recovery-command.txt"
        started_at = now_iso()
        log(
            "OrbStack recovery attempt "
            f"{attempt_number}/{len(_RECOVERY_BACKOFF_SECONDS)} "
            f"triggered by {last_readiness_reason or trigger_reason}."
        )
        collect_runtime_diagnostics_for_context(context, before_dir, env=env)
        commands, exit_code, timed_out = _run_recovery_commands(
            cwd=repo_root,
            env=env,
            trigger_reason=last_readiness_reason or trigger_reason,
            output_path=output_path,
        )
        time.sleep(backoff_seconds)
        try:
            _validate_runtime_ready(
                cwd=repo_root,
                env=env,
                max_attempts=_POST_RECOVERY_DOCKER_READY_ATTEMPTS,
            )
        except Exception as exc:  # noqa: BLE001
            last_readiness_reason = _classify_runtime_failure(cwd=repo_root, env=env, exc=exc)
            status = "failure"
        else:
            last_readiness_reason = None
            status = "success"
        collect_runtime_diagnostics_for_context(context, after_dir, env=env)
        finished_at = now_iso()
        attempts.append(
            RuntimeRecoveryAttemptReport(
                attempt=attempt_number,
                trigger_reason=trigger_reason,
                status=status,
                recovery_commands=commands,
                recovery_exit_code=exit_code,
                recovery_timed_out=timed_out,
                readiness_reason=last_readiness_reason,
                before_diagnostics_dir=str(before_dir),
                after_diagnostics_dir=str(after_dir),
                recovery_output_path=str(output_path),
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        if status == "success":
            return True, attempts, None

    return False, attempts, last_readiness_reason


def wait_runtime_ready(
    context_path: Path, *, github_env_file: Path | None = None
) -> RuntimeInstallReport:
    """Verify the OrbStack Docker runtime is ready and write the install report.

    Call this after the OrbStack setup action has run. The function runs a short
    connectivity check and writes the :class:`RuntimeInstallReport` for the scenario.
    """
    context = load_context(context_path)
    if context.platform != "macos":
        raise FreshHostError(
            "hosted docker runtime installation is only supported for macOS contexts"
        )

    repo_root = Path(context.repo_root).resolve()
    report_path = Path(context.runtime_report_path or "").resolve()
    runtime_provider = context.runtime_provider or "orbstack"
    arch = os.uname().machine
    host_cpu_count = sysctl_int("hw.ncpu")
    host_memory_bytes = sysctl_int("hw.memsize")
    host_memory_gib = (
        max(1, host_memory_bytes // 1073741824) if host_memory_bytes is not None else None
    )
    docker_config = os.environ.get("DOCKER_CONFIG", str((Path.home() / ".docker").resolve()))
    docker_host = os.environ.get("DOCKER_HOST", "")
    env = macos_env()
    env["DOCKER_CONFIG"] = docker_config
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    failure_phase: str | None = None
    failure_reason: str | None = None
    recovery_attempts: list[RuntimeRecoveryAttemptReport] = []
    started_at = now_iso()
    started = time.monotonic()

    # When OrbStack is started in the background its socket appears asynchronously.
    # Fall back to OrbStack's canonical socket path if DOCKER_HOST is not yet set.
    if not docker_host and runtime_provider == "orbstack":
        orbstack_socket = Path.home() / ".orbstack" / "run" / "docker.sock"
        docker_host = f"unix://{orbstack_socket}"
        env["DOCKER_HOST"] = docker_host

    log(f"wait_runtime_ready: DOCKER_HOST={docker_host!r} DOCKER_CONFIG={docker_config!r}")

    try:
        _validate_runtime_ready(
            cwd=repo_root,
            env=env,
            max_attempts=_INITIAL_DOCKER_READY_ATTEMPTS,
        )
    except Exception as exc:  # noqa: BLE001
        initial_reason = _classify_runtime_failure(cwd=repo_root, env=env, exc=exc)
        log(f"Initial hosted Docker runtime health gate failed: {initial_reason}.")
        if runtime_provider == "orbstack" and initial_reason in _RECOVERABLE_RUNTIME_REASONS:
            recovered, recovery_attempts, last_readiness_reason = _recover_orbstack_runtime(
                context=context,
                repo_root=repo_root,
                env=env,
                trigger_reason=initial_reason,
            )
            if not recovered:
                failure_reason = "orbstack_recovery_failed"
                failure_phase = "post_recovery_probe"
                if last_readiness_reason:
                    log(f"OrbStack recovery exhausted; last reason: {last_readiness_reason}.")
        else:
            failure_reason = initial_reason
            failure_phase = "initial_probe"

    finished_at = now_iso()
    duration_seconds = round(time.monotonic() - started, 3)

    report = RuntimeInstallReport(
        runtime_provider=runtime_provider,
        arch=arch,
        host_cpu_count=host_cpu_count,
        host_memory_gib=host_memory_gib,
        docker_host=docker_host or None,
        docker_config=docker_config,
        installed_tools=["orbstack", "docker", "docker-compose"],
        failure_reason=failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        created_at=finished_at,
        failure_phase=failure_phase,
        recovery_attempt_count=len(recovery_attempts),
        recovery_attempts=recovery_attempts,
    )
    write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if failure_reason is not None:
        main_report.failure_reason = failure_reason
        main_report.status = "failure"
    write_report(main_report, Path(context.report_path).resolve())
    github_env_vars: dict[str, str] = {
        "FRESH_HOST_HOST_CPU_COUNT": str(host_cpu_count or ""),
        "FRESH_HOST_HOST_MEMORY_GIB": str(host_memory_gib or ""),
    }
    # Export DOCKER_HOST so all subsequent workflow steps reach the same socket.
    # Without this, clawops (run-scenario) falls back to the default Docker context
    # (/var/run/docker.sock), which is absent when only OrbStack is installed.
    if docker_host:
        github_env_vars["DOCKER_HOST"] = docker_host
    write_github_env(github_env_vars, github_env_file)
    if failure_reason is not None:
        if recovery_attempts and recovery_attempts[-1].readiness_reason:
            raise FreshHostError(f"{failure_reason}: {recovery_attempts[-1].readiness_reason}")
        raise FreshHostError(failure_reason)
    return report
