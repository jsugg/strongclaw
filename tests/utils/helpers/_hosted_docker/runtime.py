"""Hosted macOS runtime installation helpers.

`wait_runtime_ready` runs inside one phase deadline: an initial readiness window, then an
OrbStack recovery slice reserved up front so a slow gate can never starve it. Every probe,
validation command, recovery command, backoff, and diagnostics collection is clipped to the
time left through one injected clock, so the phase ends at its deadline with a report.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.budget import (
    BUDGET_EXHAUSTED,
    MIN_COMMAND_SECONDS,
    RUNTIME_PHASE_MIN_SECONDS,
    RUNTIME_RECOVERY_RESERVE_SECONDS,
    SYSTEM_CLOCK,
    BudgetExhaustedError,
    Clock,
    JobBudget,
    PhaseDeadline,
    runtime_phase_deadline,
)
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

# One backoff per recovery attempt; the tuple length caps how many attempts share the slice.
_RECOVERY_BACKOFF_SECONDS: Final[tuple[int, ...]] = (5, 15)
_RECOVERY_COMMAND_TIMEOUT_SECONDS: Final[int] = 120
_RECOVERY_DIAGNOSTICS_SLICE_SECONDS: Final[int] = 30
_VALIDATION_COMMAND_TIMEOUT_SECONDS: Final[int] = 120
_READINESS_PROBE_TIMEOUT_SECONDS: Final[int] = 15
_RECOVERABLE_RUNTIME_REASONS: Final[frozenset[str]] = frozenset(
    {
        "docker_not_ready",
        "docker_probe_timeout",
        "docker_socket_eof",
        "orbstack_socket_missing",
    }
)
_RUNTIME_VALIDATION_COMMANDS: Final[tuple[list[str], ...]] = (
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


def _classify_runtime_failure(
    *,
    cwd: Path,
    env: dict[str, str],
    exc: BaseException,
    deadline: PhaseDeadline,
    clock: Clock,
) -> str:
    """Return a structured runtime failure reason; any follow-up probe stays inside *deadline*."""
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
            timeout_seconds=deadline.command_timeout(_READINESS_PROBE_TIMEOUT_SECONDS, clock),
            capture_output=True,
        )
    except BudgetExhaustedError:
        return exception_reason or "docker_not_ready"
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
    deadline: PhaseDeadline,
    clock: Clock,
) -> None:
    """Verify Docker readiness and required CLI tooling before *deadline*."""
    wait_for_docker_ready(cwd=cwd, env=env, deadline=deadline, clock=clock)
    for command in _RUNTIME_VALIDATION_COMMANDS:
        run_checked(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=deadline.command_timeout(_VALIDATION_COMMAND_TIMEOUT_SECONDS, clock),
        )


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
    deadline: PhaseDeadline,
    clock: Clock,
) -> tuple[list[list[str]], int | None, bool]:
    """Run OrbStack recovery commands inside *deadline* and capture their output."""
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
                timeout_seconds=deadline.command_timeout(_RECOVERY_COMMAND_TIMEOUT_SECONDS, clock),
                capture_output=True,
            )
        except (BudgetExhaustedError, subprocess.TimeoutExpired) as exc:
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


@dataclass(slots=True)
class _RecoveryResult:
    """Outcome of the OrbStack recovery slice."""

    recovered: bool
    attempts: list[RuntimeRecoveryAttemptReport]
    last_reason: str | None
    exhausted: bool


def _recover_orbstack_runtime(
    *,
    context: FreshHostContext,
    repo_root: Path,
    env: dict[str, str],
    trigger_reason: str,
    deadline: PhaseDeadline,
    clock: Clock,
) -> _RecoveryResult:
    """Split the recovery slice across bounded OrbStack recovery attempts.

    Each attempt gets an equal share of what is left; inside it, before and after
    diagnostics each get a fixed slice, and recovery commands, backoff, and the readiness
    gate share the rest.
    """
    attempts: list[RuntimeRecoveryAttemptReport] = []
    last_readiness_reason: str | None = trigger_reason
    exhausted = False
    recovery_root = Path(context.diagnostics_dir).resolve() / "runtime-recovery"
    attempt_total = len(_RECOVERY_BACKOFF_SECONDS)

    for attempt_number, backoff_seconds in enumerate(_RECOVERY_BACKOFF_SECONDS, start=1):
        remaining = deadline.remaining(clock)
        if remaining < MIN_COMMAND_SECONDS:
            exhausted = True
            break
        attempt_deadline = deadline.slice(
            clock,
            cap_seconds=remaining / (attempt_total - attempt_number + 1),
            limited_by="recovery-attempt",
        )
        attempt_root = recovery_root / f"attempt-{attempt_number:02d}"
        before_dir = attempt_root / "before"
        after_dir = attempt_root / "after"
        output_path = attempt_root / "recovery-command.txt"
        started_at = now_iso()
        log(
            f"OrbStack recovery attempt {attempt_number}/{attempt_total} triggered by "
            f"{last_readiness_reason or trigger_reason}; "
            f"{attempt_deadline.remaining(clock):.0f}s slice."
        )
        collect_runtime_diagnostics_for_context(
            context,
            before_dir,
            env=env,
            deadline=_diagnostics_slice(attempt_deadline, clock),
            clock=clock,
        )
        work_deadline = attempt_deadline.slice(
            clock, reserve_seconds=_RECOVERY_DIAGNOSTICS_SLICE_SECONDS
        )
        commands, exit_code, timed_out = _run_recovery_commands(
            cwd=repo_root,
            env=env,
            trigger_reason=last_readiness_reason or trigger_reason,
            output_path=output_path,
            deadline=work_deadline,
            clock=clock,
        )
        clock.sleep(work_deadline.clip(backoff_seconds, clock))
        try:
            _validate_runtime_ready(cwd=repo_root, env=env, deadline=work_deadline, clock=clock)
        except Exception as exc:  # noqa: BLE001
            exhausted = work_deadline.remaining(clock) < MIN_COMMAND_SECONDS
            last_readiness_reason = _classify_runtime_failure(
                cwd=repo_root, env=env, exc=exc, deadline=attempt_deadline, clock=clock
            )
            status = "failure"
        else:
            exhausted = False
            last_readiness_reason = None
            status = "success"
        collect_runtime_diagnostics_for_context(
            context,
            after_dir,
            env=env,
            deadline=_diagnostics_slice(attempt_deadline, clock),
            clock=clock,
        )
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
                finished_at=now_iso(),
            )
        )
        if status == "success":
            return _RecoveryResult(
                recovered=True, attempts=attempts, last_reason=None, exhausted=False
            )

    return _RecoveryResult(
        recovered=False,
        attempts=attempts,
        last_reason=last_readiness_reason,
        exhausted=exhausted,
    )


def _diagnostics_slice(deadline: PhaseDeadline, clock: Clock) -> PhaseDeadline:
    """Return the fixed diagnostics slice at the start of *deadline*."""
    return deadline.slice(
        clock, cap_seconds=_RECOVERY_DIAGNOSTICS_SLICE_SECONDS, limited_by="diagnostics-slice"
    )


def _initial_window(deadline: PhaseDeadline, clock: Clock) -> PhaseDeadline:
    """Return the initial readiness window, leaving the recovery reserve after it.

    A phase shorter than its minimum shrinks both parts proportionally, so recovery still
    gets its share of whatever time the job budget left.
    """
    scale = min(1.0, deadline.remaining(clock) / RUNTIME_PHASE_MIN_SECONDS)
    return deadline.slice(clock, reserve_seconds=RUNTIME_RECOVERY_RESERVE_SECONDS * scale)


@dataclass(slots=True)
class _RuntimeOutcome:
    """Classified result of the runtime phase."""

    failure_reason: str | None
    failure_phase: str | None
    last_failure_reason: str | None
    recovery_attempts: list[RuntimeRecoveryAttemptReport]


def _bring_up_runtime(
    *,
    context: FreshHostContext,
    repo_root: Path,
    env: dict[str, str],
    runtime_provider: str,
    deadline: PhaseDeadline,
    clock: Clock,
) -> _RuntimeOutcome:
    """Run the initial readiness window, then OrbStack recovery, inside *deadline*."""
    try:
        _validate_runtime_ready(
            cwd=repo_root, env=env, deadline=_initial_window(deadline, clock), clock=clock
        )
    except Exception as exc:  # noqa: BLE001
        initial_reason = _classify_runtime_failure(
            cwd=repo_root, env=env, exc=exc, deadline=deadline, clock=clock
        )
    else:
        return _RuntimeOutcome(None, None, None, [])

    log(f"Initial hosted Docker runtime health gate failed: {initial_reason}.")
    if runtime_provider != "orbstack" or initial_reason not in _RECOVERABLE_RUNTIME_REASONS:
        return _RuntimeOutcome(initial_reason, "initial_probe", initial_reason, [])

    recovery = _recover_orbstack_runtime(
        context=context,
        repo_root=repo_root,
        env=env,
        trigger_reason=initial_reason,
        deadline=deadline,
        clock=clock,
    )
    if recovery.recovered:
        return _RuntimeOutcome(None, None, None, recovery.attempts)
    last_reason = recovery.last_reason or initial_reason
    failure_reason = BUDGET_EXHAUSTED if recovery.exhausted else "orbstack_recovery_failed"
    log(f"OrbStack recovery ended with {failure_reason}; last reason: {last_reason}.")
    return _RuntimeOutcome(
        failure_reason,
        "post_recovery_probe" if recovery.attempts else "initial_probe",
        last_reason,
        recovery.attempts,
    )


def wait_runtime_ready(
    context_path: Path,
    *,
    github_env_file: Path | None = None,
    deadline: PhaseDeadline | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> RuntimeInstallReport:
    """Verify the OrbStack Docker runtime is ready and write the install report.

    Call this after the OrbStack setup action has run. *deadline* defaults to the runtime
    phase of the recorded job budget; the image phase passes a slice of its own deadline
    when it restarts the runtime, so recovery there cannot reset the budget.
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

    started_at = now_iso()
    started = clock.monotonic()

    # When OrbStack is started in the background its socket appears asynchronously.
    # Fall back to OrbStack's canonical socket path if DOCKER_HOST is not yet set.
    if not docker_host and runtime_provider == "orbstack":
        orbstack_socket = Path.home() / ".orbstack" / "run" / "docker.sock"
        docker_host = f"unix://{orbstack_socket}"
        env["DOCKER_HOST"] = docker_host

    log(f"wait_runtime_ready: DOCKER_HOST={docker_host!r} DOCKER_CONFIG={docker_config!r}")

    phase_error: BudgetExhaustedError | None = None
    phase = deadline
    if phase is None:
        try:
            phase = runtime_phase_deadline(
                JobBudget.from_env(os.environ if environ is None else environ), clock=clock
            )
        except BudgetExhaustedError as exc:
            phase_error = exc
    if phase is None:
        log(f"Runtime phase cannot start: {phase_error}.")
        outcome = _RuntimeOutcome(BUDGET_EXHAUSTED, "phase_start", None, [])
    else:
        log(f"Runtime phase: {phase.remaining(clock):.0f}s, limited by {phase.limited_by}.")
        outcome = _bring_up_runtime(
            context=context,
            repo_root=repo_root,
            env=env,
            runtime_provider=runtime_provider,
            deadline=phase,
            clock=clock,
        )

    finished_at = now_iso()
    report = RuntimeInstallReport(
        runtime_provider=runtime_provider,
        arch=arch,
        host_cpu_count=host_cpu_count,
        host_memory_gib=host_memory_gib,
        docker_host=docker_host or None,
        docker_config=docker_config,
        installed_tools=["orbstack", "docker", "docker-compose"],
        failure_reason=outcome.failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(clock.monotonic() - started, 3),
        created_at=finished_at,
        failure_phase=outcome.failure_phase,
        recovery_attempt_count=len(outcome.recovery_attempts),
        recovery_attempts=outcome.recovery_attempts,
        last_failure_reason=outcome.last_failure_reason,
        deadline_epoch=None if phase is None else phase.wall_deadline_epoch,
        deadline_limited_by=None if phase is None else phase.limited_by,
    )
    write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if outcome.failure_reason is not None:
        main_report.failure_reason = outcome.failure_reason
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
    _raise_for_failure(outcome, phase=phase, phase_error=phase_error)
    return report


def _raise_for_failure(
    outcome: _RuntimeOutcome,
    *,
    phase: PhaseDeadline | None,
    phase_error: BudgetExhaustedError | None,
) -> None:
    """Raise the classified runtime failure, if any, after its evidence is written."""
    if phase_error is not None:
        raise phase_error
    if outcome.failure_reason is None:
        return
    if outcome.failure_reason == BUDGET_EXHAUSTED:
        limited_by = "unknown" if phase is None else phase.limited_by
        raise BudgetExhaustedError(
            f"runtime phase ({limited_by}) ended before Docker was ready; "
            f"last reason: {outcome.last_failure_reason}"
        )
    if outcome.recovery_attempts and outcome.recovery_attempts[-1].readiness_reason:
        raise FreshHostError(
            f"{outcome.failure_reason}: {outcome.recovery_attempts[-1].readiness_reason}"
        )
    raise FreshHostError(outcome.failure_reason)
