"""Unit coverage for hosted macOS runtime-loss recovery inside fresh-host scenarios."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from clawops.strongclaw_runtime import DockerBackendProbe
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import faults as fresh_host_faults
from tests.utils.helpers._fresh_host import macos as fresh_host_macos
from tests.utils.helpers._fresh_host import runtime_loss
from tests.utils.helpers._fresh_host import shell as fresh_host_shell
from tests.utils.helpers._fresh_host.budget import (
    SCENARIO_RECOVERY_CAP_SECONDS,
    SCENARIO_RETRY_RESERVE_SECONDS,
    BudgetExhaustedError,
    Clock,
    JobBudget,
    PhaseDeadline,
    initialize_job_budget,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._hosted_docker import diagnostics as hosted_docker_diagnostics
from tests.utils.helpers._hosted_docker import runtime as hosted_docker_runtime
from tests.utils.helpers.clock import FakeClock

_EOF_STDERR = 'error during connect: Get "http://docker.sock/_ping": EOF'
_STEP = "exercise-sidecars"


class _FakeOrbStack:
    """Scripted OrbStack runtime: once down, it stays down until a recovery command runs."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        up: bool,
        recovers: bool = True,
        command_seconds: float = 0.0,
        docker_info_error: BaseException | None = None,
    ) -> None:
        self.clock = clock
        self.up = up
        self.recovers = recovers
        self.command_seconds = command_seconds
        self.docker_info_error = docker_info_error
        self.commands: list[list[str]] = []

    def run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        self.commands.append(list(command))
        self.clock.advance(self.command_seconds)
        if command == ["docker", "info"]:
            if self.docker_info_error is not None:
                raise self.docker_info_error
            if not self.up:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=_EOF_STDERR)
        if command in (["orb", "start"], ["orb", "restart", "docker"]):
            self.up = self.recovers
        if command == ["orb", "stop"]:
            self.up = False
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def run_checked(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        completed = self.run_command(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            capture_output=capture_output,
        )
        if completed.returncode != 0:
            raise FreshHostError(f"{' '.join(command)} failed: {completed.stderr}")
        return completed

    def wait_for_docker_ready(
        self, *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env, deadline, clock
        if not self.up:
            raise FreshHostError(f"docker did not become ready; last: {_EOF_STDERR}")

    @property
    def orb_commands(self) -> list[list[str]]:
        return [command for command in self.commands if command[0] == "orb"]


def _install(test_context: TestContext, orbstack: _FakeOrbStack, tmp_path: Path) -> None:
    """Route every runtime, recovery, and diagnostics command through *orbstack*."""
    test_context.patch.patch_object(hosted_docker_runtime, "run_command", new=orbstack.run_command)
    test_context.patch.patch_object(hosted_docker_runtime, "run_checked", new=orbstack.run_checked)
    test_context.patch.patch_object(
        hosted_docker_runtime, "wait_for_docker_ready", new=orbstack.wait_for_docker_ready
    )
    test_context.patch.patch_object(
        hosted_docker_diagnostics, "run_command", new=orbstack.run_command
    )
    test_context.patch.patch_object(hosted_docker_diagnostics, "sysctl_int", new=_fake_sysctl_int)
    test_context.patch.patch_object(
        hosted_docker_diagnostics, "_orbstack_log_dir", new=lambda: tmp_path / "no-orbstack-logs"
    )


def _fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
    del name, timeout_seconds
    return 4


def _no_op(**kwargs: object) -> None:
    del kwargs


def _context(
    tmp_path: Path, test_context: TestContext, *, scenario_id: str = "macos-sidecars"
) -> FreshHostContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles(
        "fresh_host_push" if scenario_id == "linux" else "fresh_host_macos_orbstack"
    )
    # A TCP host skips the socket check, so only the scripted `docker info` decides health.
    test_context.env.set("DOCKER_HOST", "tcp://127.0.0.1:2375")
    return fresh_host.prepare_context(
        scenario_id=scenario_id,
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=tmp_path / "github.env",
    )


def _budget(clock: FakeClock) -> JobBudget:
    return initialize_job_budget(
        job_timeout_minutes=90,
        scenario_reserve_minutes=35,
        observer_reserve_minutes=15,
        environ={},
        clock=clock,
    )


def _window(context: FreshHostContext, clock: FakeClock) -> runtime_loss.RecoveryWindow:
    window = runtime_loss.arm_runtime_recovery(
        context, environ=_budget(clock).to_env(), clock=clock
    )
    assert window is not None
    return window


class _Step:
    """A scenario step that fails a scripted number of times, then returns its command."""

    def __init__(self, *, failures: int, error: str = "Command failed with exit 1") -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self) -> list[str]:
        self.calls += 1
        if self.calls <= self.failures:
            raise FreshHostError(self.error)
        return ["step", "ok"]


def _loss_report(context: FreshHostContext) -> dict[str, object]:
    path = Path(context.diagnostics_dir) / "runtime-loss" / _STEP / "report.json"
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return {str(key): value for key, value in cast(dict[object, object], payload).items()}


def _attempts(report: dict[str, object]) -> list[dict[str, object]]:
    attempts = report["recovery_attempts"]
    assert isinstance(attempts, list)
    return cast(list[dict[str, object]], attempts)


@pytest.mark.parametrize(
    ("scenario_id", "with_budget", "armed"),
    [
        ("macos-sidecars", True, True),
        ("macos-sidecars", False, False),
        ("linux", True, False),
    ],
)
def test_scenario_recovery_deadline_arms_only_budgeted_hosted_macos_runs(
    tmp_path: Path,
    test_context: TestContext,
    scenario_id: str,
    with_budget: bool,
    armed: bool,
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context, scenario_id=scenario_id)
    environ = _budget(clock).to_env() if with_budget else {}

    window = runtime_loss.arm_runtime_recovery(context, environ=environ, clock=clock)

    assert (window is not None) is armed
    if window is not None:
        assert window.clock is clock
        assert window.deadline.remaining(clock) == 35 * 60


def test_scenario_recovery_deadline_disarms_inside_the_observer_reserve(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    budget = _budget(clock)
    clock.advance(budget.deadline_epoch - budget.observer_reserve_seconds - clock.time())

    assert runtime_loss.arm_runtime_recovery(context, environ=budget.to_env(), clock=clock) is None


def test_failures_pass_through_untouched_without_a_recovery_deadline(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    step = _Step(failures=1)

    with pytest.raises(FreshHostError, match="Command failed with exit 1") as raised:
        runtime_loss.run_with_runtime_recovery(context, step=_STEP, window=None, action=step)

    assert raised.value.__cause__ is None
    assert step.calls == 1
    assert orbstack.commands == []


def test_linux_scenarios_fail_fast_even_with_a_recorded_budget(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context, scenario_id="linux")
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    window = runtime_loss.arm_runtime_recovery(
        context, environ=_budget(clock).to_env(), clock=clock
    )
    step = _Step(failures=1)

    assert window is None
    with pytest.raises(FreshHostError, match="Command failed with exit 1"):
        runtime_loss.run_with_runtime_recovery(context, step=_STEP, window=window, action=step)

    assert step.calls == 1
    assert orbstack.commands == []


def test_step_failures_with_a_reachable_backend_are_not_runtime_loss(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=True)
    _install(test_context, orbstack, tmp_path)
    # Timeout wording in a step failure must not be mistaken for a dead runtime.
    step = _Step(failures=1, error="timed out waiting for compose service 'litellm'")

    with pytest.raises(FreshHostError, match="timed out waiting for compose service"):
        runtime_loss.run_with_runtime_recovery(
            context,
            step=_STEP,
            window=_window(context, clock),
            action=step,
        )

    assert step.calls == 1
    assert orbstack.commands == [["docker", "info"]]
    assert not (Path(context.diagnostics_dir) / "runtime-loss").exists()


def test_socket_eof_runtime_loss_restarts_orbstack_and_retries_the_step_once(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    step = _Step(failures=1)

    result = runtime_loss.run_with_runtime_recovery(
        context,
        step=_STEP,
        window=_window(context, clock),
        action=step,
    )

    assert result == ["step", "ok"]
    assert step.calls == 2
    report = _loss_report(context)
    attempts = _attempts(report)
    assert len(attempts) == 1
    assert attempts[0]["recovery_commands"] == [["orb", "restart", "docker"]]
    assert report["step"] == _STEP
    assert report["trigger_reason"] == "docker_socket_eof"
    assert report["recovered"] is True
    assert report["exhausted"] is False
    assert report["deadline_limited_by"] == runtime_loss.RUNTIME_LOSS_CAP
    attempt_root = Path(context.diagnostics_dir) / "runtime-loss" / _STEP / "attempt-01"
    assert (attempt_root / "before").is_dir()
    assert (attempt_root / "after").is_dir()
    assert (attempt_root / "recovery-command.txt").is_file()


def test_missing_orbstack_socket_starts_orbstack_instead_of_restarting_docker(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    test_context.env.set("DOCKER_HOST", f"unix://{tmp_path / 'missing.sock'}")
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    step = _Step(failures=1)

    runtime_loss.run_with_runtime_recovery(
        context,
        step=_STEP,
        window=_window(context, clock),
        action=step,
    )

    assert ["orb", "start"] in orbstack.orb_commands
    assert ["orb", "restart", "docker"] not in orbstack.orb_commands
    assert _loss_report(context)["trigger_reason"] == "orbstack_socket_missing"


def test_retry_after_recovery_runs_the_retry_action(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    _install(test_context, _FakeOrbStack(clock, up=False), tmp_path)
    step = _Step(failures=1)
    retried: list[str] = []

    def retry() -> list[str]:
        retried.append("retry")
        return ["retry", "ok"]

    result = runtime_loss.run_with_runtime_recovery(
        context,
        step=_STEP,
        window=_window(context, clock),
        action=step,
        retry_action=retry,
    )

    assert result == ["retry", "ok"]
    assert step.calls == 1
    assert retried == ["retry"]


def test_recovery_refuses_to_start_once_only_the_retry_reserve_is_left(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    window = _window(context, clock)
    clock.advance(window.deadline.remaining(clock) - SCENARIO_RETRY_RESERVE_SECONDS)
    step = _Step(failures=1)

    with pytest.raises(BudgetExhaustedError, match="runtime_loss recovery for") as raised:
        runtime_loss.run_with_runtime_recovery(context, step=_STEP, window=window, action=step)

    assert str(raised.value).startswith("budget_exhausted: ")
    assert isinstance(raised.value.__cause__, FreshHostError)
    assert step.calls == 1
    assert orbstack.commands == []


def test_recovery_that_outlives_its_slice_ends_with_budget_exhaustion(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    # Every command burns most of an attempt's share, so the slice runs out mid-recovery.
    orbstack = _FakeOrbStack(clock, up=False, command_seconds=SCENARIO_RECOVERY_CAP_SECONDS / 3)
    _install(test_context, orbstack, tmp_path)
    window = _window(context, clock)
    started = clock.monotonic()
    step = _Step(failures=1)

    with pytest.raises(BudgetExhaustedError, match="recovery ran out of time"):
        runtime_loss.run_with_runtime_recovery(context, step=_STEP, window=window, action=step)

    assert step.calls == 1
    report = _loss_report(context)
    assert report["recovered"] is False
    assert report["exhausted"] is True
    # The slice bounds recovery; one in-flight command may finish past it, never two.
    assert clock.monotonic() - started <= SCENARIO_RECOVERY_CAP_SECONDS + orbstack.command_seconds


def test_orbstack_that_never_recovers_fails_the_step_with_attempt_evidence(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False, recovers=False)
    _install(test_context, orbstack, tmp_path)
    step = _Step(failures=1)

    with pytest.raises(
        FreshHostError,
        match=r"orbstack_recovery_failed: runtime_loss during exercise-sidecars "
        r"\(docker_socket_eof\)",
    ):
        runtime_loss.run_with_runtime_recovery(
            context,
            step=_STEP,
            window=_window(context, clock),
            action=step,
        )

    assert step.calls == 1
    report = _loss_report(context)
    assert report["recovered"] is False
    assert report["exhausted"] is False
    assert len(_attempts(report)) == 2


def test_unrecoverable_runtime_loss_is_reported_without_restarting_orbstack(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False, docker_info_error=FileNotFoundError("docker"))
    _install(test_context, orbstack, tmp_path)
    step = _Step(failures=1)

    with pytest.raises(FreshHostError, match=r"unrecoverable runtime_loss .*docker_binary_missing"):
        runtime_loss.run_with_runtime_recovery(
            context,
            step=_STEP,
            window=_window(context, clock),
            action=step,
        )

    assert step.calls == 1
    assert orbstack.orb_commands == []


def test_macos_sidecars_phase_recovers_from_an_injected_orbstack_stop(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    test_context.env.set(fresh_host_faults.FAULT_INJECTION_ENV, "orbstack-stop-during-sidecars")
    test_context.env.set("GITHUB_EVENT_NAME", "workflow_dispatch")
    test_context.env.set("GITHUB_REF", "refs/heads/fix/runtime-loss")
    orbstack = _FakeOrbStack(clock, up=True)
    _install(test_context, orbstack, tmp_path)
    calls: list[str] = []

    def fake_fault_run_command(command: list[str], **kwargs: object) -> None:
        del kwargs
        calls.append(" ".join(command))
        orbstack.run_command(command, cwd=tmp_path, env={})

    def fake_run_command(command: list[str], **kwargs: object) -> None:
        del kwargs
        calls.append(command[-2] if "--repo-local-state" in command else " ".join(command))
        if not orbstack.up:
            raise FreshHostError("Docker backend is not reachable from this shell.")

    def fake_verify(compose_file: Path, **kwargs: object) -> None:
        del kwargs
        calls.append(f"verify:{compose_file.name}")

    test_context.patch.patch_object(fresh_host_faults, "run_command", new=fake_fault_run_command)
    test_context.patch.patch_object(fresh_host_macos, "run_command", new=fake_run_command)
    test_context.patch.patch_object(fresh_host_macos, "wait_for_docker_backend", new=_no_op)
    test_context.patch.patch_object(
        fresh_host_macos, "verify_sidecar_services_running", new=fake_verify
    )

    fresh_host_macos.exercise_macos_sidecars(context, recovery_window=_window(context, clock))

    assert calls == [
        "orb stop",
        "up",
        "up",
        "verify:docker-compose.aux-stack.ci-hosted-macos.yaml",
    ]
    assert ["orb", "restart", "docker"] in orbstack.orb_commands
    assert _loss_report(context)["recovered"] is True
    marker = (
        Path(context.diagnostics_dir) / "fault-injection" / "orbstack-stop-during-sidecars.json"
    )
    assert marker.is_file()


def test_backend_lost_inside_a_health_wait_fails_fast_into_recovery_and_one_retry(
    tmp_path: Path, test_context: TestContext
) -> None:
    clock = FakeClock()
    context = _context(tmp_path, test_context)
    orbstack = _FakeOrbStack(clock, up=False)
    _install(test_context, orbstack, tmp_path)
    test_context.patch.patch_object(fresh_host_shell, "time", new=clock)

    def fake_compose_service_entries(
        compose_file: Path, **kwargs: object
    ) -> dict[str, dict[str, object]]:
        del compose_file, kwargs
        if not orbstack.up:
            raise fresh_host_shell.ComposeStatusCommandError(_EOF_STDERR)
        return {
            name: {"Service": name, "State": "running", "Health": "healthy"}
            for name in fresh_host_shell.SIDECAR_EXPECTED_SERVICES
        }

    def fake_probe_docker_backend(**kwargs: object) -> DockerBackendProbe:
        del kwargs
        return DockerBackendProbe(reachable=orbstack.up, detail="`docker info` failed: EOF")

    test_context.patch.patch_object(
        fresh_host_shell, "compose_service_entries", new=fake_compose_service_entries
    )
    test_context.patch.patch_object(
        fresh_host_shell, "probe_docker_backend", new=fake_probe_docker_backend
    )
    wait_seconds: list[float] = []

    def health_wait() -> list[str]:
        started = clock.monotonic()
        try:
            fresh_host_shell.verify_sidecar_services_running(
                tmp_path / "compose.yaml", cwd=tmp_path, env={}, timeout_seconds=300
            )
        finally:
            wait_seconds.append(clock.monotonic() - started)
        return ["verify", "ok"]

    result = runtime_loss.run_with_runtime_recovery(
        context, step=_STEP, window=_window(context, clock), action=health_wait
    )

    assert result == ["verify", "ok"]
    assert wait_seconds[0] == pytest.approx(4.0)
    assert len(wait_seconds) == 2
    report = _loss_report(context)
    assert report["recovered"] is True
    assert report["trigger_reason"] == "docker_socket_eof"
