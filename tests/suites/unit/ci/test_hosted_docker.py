"""Unit coverage for hosted Docker CI helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Protocol, cast

import pytest

from clawops.strongclaw_runtime import varlock_local_env_file, write_env_assignments
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import fresh_host, hosted_docker
from tests.utils.helpers._fresh_host.budget import (
    RUNTIME_PHASE_CAP_SECONDS,
    RUNTIME_RECOVERY_RESERVE_SECONDS,
    BudgetExhaustedError,
    Clock,
    JobBudget,
    PhaseDeadline,
    initialize_job_budget,
)
from tests.utils.helpers._fresh_host.shell import phase_env
from tests.utils.helpers._hosted_docker import diagnostics as hosted_docker_diagnostics
from tests.utils.helpers._hosted_docker import images as hosted_docker_images
from tests.utils.helpers._hosted_docker import process as hosted_docker_process
from tests.utils.helpers._hosted_docker import runtime as hosted_docker_runtime
from tests.utils.helpers._hosted_docker import shell as hosted_docker_shell
from tests.utils.helpers._hosted_docker.events import PullEventLog, read_events
from tests.utils.helpers._hosted_docker.models import PULL_RECOVERY_CAP_SECONDS
from tests.utils.helpers.clock import FakeClock


class _PullOneImage(Protocol):
    def __call__(self, image: str, timeout_seconds: int) -> tuple[str, int, float, str]: ...


class _WaitForDockerReady(Protocol):
    def __call__(
        self, *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None: ...


_wait_for_docker_ready = cast(
    _WaitForDockerReady,
    cast(Any, hosted_docker)._wait_for_docker_ready,
)
_ORBSTACK_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="OrbStack is only available on macOS.",
)


@pytest.fixture(autouse=True)
def _skip_linux_disk_preflight(test_context: TestContext) -> None:
    """Keep pull-focused tests off the host disk; the preflight has its own suite."""

    def no_preflight(**kwargs: object) -> None:
        del kwargs

    test_context.patch.patch_object(hosted_docker_images, "preflight_linux_disk", new=no_preflight)


def _no_jitter(cap_seconds: float) -> float:
    return cap_seconds


class _FunctionPullClient:
    """Adapt a fake ``(image, timeout) -> (image, rc, duration, output)`` pull into a client."""

    def __init__(self, pull: _PullOneImage) -> None:
        self._pull = pull

    def pull(
        self, image: str, *, timeout_seconds: float, stall_seconds: float
    ) -> hosted_docker.PullOutcome:
        del stall_seconds
        pulled, returncode, duration, output = self._pull(image, int(timeout_seconds))
        return hosted_docker.PullOutcome(
            image=pulled,
            returncode=returncode,
            duration_seconds=duration,
            output_tail=output,
            failure_class=None if returncode == 0 else hosted_docker.classify_pull_failure(output),
        )


def test_pull_images_retries_with_reduced_parallelism(
    test_context: TestContext,
) -> None:
    """Image pulls should retry failures with reduced parallelism."""
    attempts: dict[str, int] = {"postgres:16": 0, "qdrant:v1": 0}

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if image == "postgres:16" and attempts[image] == 1:
            return image, 1, 0.1, "unexpected EOF"
        return image, 0, 0.1, ""

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["postgres:16", "qdrant:v1"],
        parallelism=4,
        max_attempts=3,
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert report.retried_images == ["postgres:16"]
    assert set(report.pulled_images) == {"postgres:16", "qdrant:v1"}


def test_pull_images_waits_for_daemon_recovery_on_connectivity_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Image pulls should probe daemon recovery after connectivity failures."""
    attempts: dict[str, int] = {"postgres:16": 0}
    recovery_probes: list[tuple[Path, dict[str, str], float]] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if attempts[image] == 1:
            return (
                image,
                1,
                0.1,
                (
                    "Error response from daemon: Unavailable: connection error: "
                    'desc = "transport: Error while dialing: dial unix '
                    '/run/containerd/containerd.sock: connect: connection refused"'
                ),
            )
        return image, 0, 0.1, ""

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        recovery_probes.append((cwd, dict(env), deadline.remaining(clock)))

    test_context.patch.patch_object(
        hosted_docker_images,
        "wait_for_docker_ready",
        new=fake_wait_for_docker_ready,
    )

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=2,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert recovery_probes == [
        (tmp_path, {"DOCKER_HOST": "unix:///tmp/docker.sock"}, PULL_RECOVERY_CAP_SECONDS)
    ]


def test_pull_images_probes_runtime_before_retry_for_non_connectivity_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Image pull retries should probe Docker runtime before retrying regardless of error signature."""
    attempts: dict[str, int] = {"ubuntu/squid": 0}
    recovery_probes: list[tuple[Path, dict[str, str], float]] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if attempts[image] == 1:
            return (
                image,
                1,
                0.1,
                (
                    'Get "https://registry-1.docker.io/v2/ubuntu/squid/manifests/sha256:deadbeef": '
                    "tls: failed to verify certificate: x509: certificate signed by unknown authority"
                ),
            )
        return image, 0, 0.1, ""

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        recovery_probes.append((cwd, dict(env), deadline.remaining(clock)))

    test_context.patch.patch_object(
        hosted_docker_images,
        "wait_for_docker_ready",
        new=fake_wait_for_docker_ready,
    )

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["ubuntu/squid"],
        parallelism=2,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert recovery_probes == [
        (tmp_path, {"DOCKER_HOST": "unix:///tmp/docker.sock"}, PULL_RECOVERY_CAP_SECONDS)
    ]


def test_pull_images_classifies_docker_socket_eof_as_connectivity_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Docker socket EOF failures should be classified as daemon connectivity issues."""
    attempts: dict[str, int] = {"ubuntu/squid": 0}
    logs: list[str] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if attempts[image] == 1:
            return (
                image,
                1,
                0.1,
                (
                    'error during connect: Post "http://%2FUsers%2Frunner%2F.orbstack%2Frun%2Fdocker.sock'
                    '/v1.54/images/create?fromImage=docker.io%2Fubuntu%2Fsquid": EOF'
                ),
            )
        return image, 0, 0.1, ""

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env, deadline, clock

    def fake_log(message: str) -> None:
        logs.append(message)

    test_context.patch.patch_object(
        hosted_docker_images,
        "wait_for_docker_ready",
        new=fake_wait_for_docker_ready,
    )
    test_context.patch.patch_object(hosted_docker_images, "log", new=fake_log)

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["ubuntu/squid"],
        parallelism=2,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert any(
        "Detected Docker daemon connectivity failure; waiting for runtime recovery." in entry
        for entry in logs
    )


def test_pull_images_uses_extended_backoff_for_transient_registry_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Transient registry timeouts should retry with a longer, capped exponential backoff."""
    attempts: dict[str, int] = {"qdrant/qdrant:v1": 0}
    logs: list[str] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if attempts[image] <= 4:
            return (
                image,
                1,
                15.0,
                (
                    'Error response from daemon: Get "https://registry-1.docker.io/v2/": '
                    "context deadline exceeded (Client.Timeout exceeded while awaiting headers)"
                ),
            )
        return image, 0, 0.1, ""

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env, deadline, clock

    def fake_log(message: str) -> None:
        logs.append(message)

    test_context.patch.patch_object(
        hosted_docker_images,
        "wait_for_docker_ready",
        new=fake_wait_for_docker_ready,
    )
    test_context.patch.patch_object(hosted_docker_images, "log", new=fake_log)

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["qdrant/qdrant:v1"],
        parallelism=2,
        max_attempts=5,
        recovery_cwd=tmp_path,
        recovery_env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 5
    assert report.retried_images == ["qdrant/qdrant:v1"]
    # Exponential growth from the 10s base, capped at 45s — far longer than the 2s/4s
    # generic backoff so a brief Docker Hub outage is ridden out across the retry budget.
    assert clock.sleeps == [10, 20, 40, 45]
    assert any(
        "Detected transient registry connectivity failure; backing off before retry." in entry
        for entry in logs
    )


def test_pull_images_requires_recovery_cwd_and_env_together() -> None:
    """Pull recovery wiring should reject partial recovery configuration."""
    with pytest.raises(fresh_host.FreshHostError, match="recovery_cwd and recovery_env"):
        hosted_docker.pull_images(
            ["postgres:16"],
            parallelism=1,
            max_attempts=1,
            recovery_cwd=Path("/tmp"),
        )


def test_resolve_compose_images_uses_first_seen_order(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Compose image resolution should preserve first-seen order and de-duplicate refs."""
    compose_a = tmp_path / "a.yaml"
    compose_b = tmp_path / "b.yaml"
    compose_a.write_text("services: {}\n", encoding="utf-8")
    compose_b.write_text("services: {}\n", encoding="utf-8")

    outputs = {
        compose_a: "postgres:16\nqdrant:v1\n",
        compose_b: "qdrant:v1\nbrowserlab:latest\n",
    }

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        compose_path = Path(command[3])
        return subprocess.CompletedProcess(command, 0, stdout=outputs[compose_path], stderr="")

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)

    images = hosted_docker.resolve_compose_images(
        [compose_a, compose_b],
        cwd=tmp_path,
        env={"STRONGCLAW_COMPOSE_STATE_DIR": str(tmp_path / "state")},
    )

    assert images == ["postgres:16", "qdrant:v1", "browserlab:latest"]


def test_ensure_images_warms_linux_scenario_images(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Linux fresh-host contexts should prewarm compose images before scenario execution."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    pulled_payload: dict[str, object] = {}

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del env
        assert capture_output is True
        assert timeout_seconds == 120
        assert cwd == workspace.resolve()
        return subprocess.CompletedProcess(command, 0, stdout="postgres:16\nbrowserlab:latest\n")

    list_calls = {"count": 0}

    def fake_list_local_images(images: list[str], **kwargs: object) -> list[str]:
        del kwargs
        list_calls["count"] += 1
        if list_calls["count"] == 1:
            return []
        return list(images)

    def fake_pull_images(
        images: list[str],
        *,
        parallelism: int,
        max_attempts: int,
        pull_timeout_seconds: int = hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
        recovery_cwd: Path | None = None,
        recovery_env: dict[str, str] | None = None,
        deadline: PhaseDeadline | None = None,
        clock: object = None,
        event_log: object = None,
        recover_runtime: object = None,
    ) -> hosted_docker.PullReport:
        del clock, event_log
        pulled_payload["deadline"] = deadline
        pulled_payload["recover_runtime"] = recover_runtime
        pulled_payload["images"] = list(images)
        pulled_payload["parallelism"] = parallelism
        pulled_payload["max_attempts"] = max_attempts
        pulled_payload["pull_timeout_seconds"] = pull_timeout_seconds
        pulled_payload["recovery_cwd"] = recovery_cwd
        pulled_payload["recovery_env"] = dict(recovery_env or {})
        return hosted_docker.PullReport(
            exit_code=0,
            pulled_images=list(images),
            failed_images=[],
            attempt_count=1,
            retried_images=[],
        )

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(
        hosted_docker_images, "list_local_images", new=fake_list_local_images
    )
    test_context.patch.patch_object(hosted_docker_images, "pull_images", new=fake_pull_images)

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == ["postgres:16", "browserlab:latest"]
    assert report.missing_before_pull == ["postgres:16", "browserlab:latest"]
    assert report.pull_attempt_count == 1
    assert report.missing_after_pull == []
    assert pulled_payload["images"] == ["postgres:16", "browserlab:latest"]
    assert pulled_payload["parallelism"] == context.docker_pull_parallelism
    assert pulled_payload["max_attempts"] == context.docker_pull_max_attempts
    assert (
        pulled_payload["pull_timeout_seconds"] == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
    )
    assert pulled_payload["recovery_cwd"] == workspace.resolve()
    assert isinstance(pulled_payload["deadline"], PhaseDeadline)
    # Linux has no OrbStack runtime to restart; recovery stays a readiness wait.
    assert pulled_payload["recover_runtime"] is None
    recovery_env = cast(dict[str, str], pulled_payload["recovery_env"])
    assert recovery_env["NEO4J_PASSWORD"].strip() != ""
    assert recovery_env["LITELLM_DB_PASSWORD"].strip() != ""
    assert recovery_env["STRONGCLAW_COMPOSE_STATE_DIR"].endswith("/compose-prepull")
    assert context.image_report_path is not None
    assert Path(context.image_report_path).is_file()
    image_report_payload = json.loads(Path(context.image_report_path).read_text(encoding="utf-8"))
    assert isinstance(image_report_payload.get("started_at"), str)
    assert isinstance(image_report_payload.get("finished_at"), str)
    assert isinstance(image_report_payload.get("duration_seconds"), (int, float))
    assert cast(float, image_report_payload["duration_seconds"]) >= 0


def test_ensure_images_inherits_repo_local_varlock_assignments(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Image resolution should honor repo-local Varlock compose secrets."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")

    local_env_file = varlock_local_env_file(workspace)
    local_env_file.parent.mkdir(parents=True, exist_ok=True)
    write_env_assignments(
        local_env_file,
        {
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "sidecar-secret",
        },
    )
    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert timeout_seconds == 120
        assert cwd == workspace.resolve()
        assert env["NEO4J_USERNAME"] == "neo4j"
        assert env["NEO4J_PASSWORD"] == "sidecar-secret"
        assert env["STRONGCLAW_COMPOSE_VARIANT"] == "ci-hosted-macos"
        assert env["STRONGCLAW_COMPOSE_STATE_DIR"].endswith("/compose-prepull")
        return subprocess.CompletedProcess(command, 0, stdout="postgres:16\n", stderr="")

    def fake_list_local_images(images: list[str], **kwargs: object) -> list[str]:
        del kwargs
        return list(images)

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(
        hosted_docker_images, "list_local_images", new=fake_list_local_images
    )

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == ["postgres:16"]
    assert report.missing_before_pull == []
    assert report.pull_attempt_count == 0


def test_ensure_images_uses_compose_resolution_placeholders_before_setup(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Image resolution should prefill required compose secrets before setup runs."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.set("VARLOCK_LOCAL_ENV_FILE", str(tmp_path / "missing.env"))

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert timeout_seconds == 120
        assert cwd == workspace.resolve()
        assert env["NEO4J_PASSWORD"] == hosted_docker_images.COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER
        assert (
            env["LITELLM_DB_PASSWORD"] == hosted_docker_images.COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER
        )
        return subprocess.CompletedProcess(command, 0, stdout="postgres:16\n", stderr="")

    def fake_list_local_images(images: list[str], **kwargs: object) -> list[str]:
        del kwargs
        return list(images)

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(
        hosted_docker_images, "list_local_images", new=fake_list_local_images
    )

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == ["postgres:16"]
    assert report.missing_before_pull == []
    assert report.pull_attempt_count == 0


def test_wait_for_docker_ready_retries_after_probe_timeout(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Docker readiness probes should tolerate transient probe timeouts."""
    attempts = {"count": 0}

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "info"]
        assert cwd == tmp_path
        assert env == {"DOCKER_HOST": "unix:///tmp/docker.sock"}
        assert timeout_seconds == 30
        assert capture_output is True
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        return subprocess.CompletedProcess(command, 0, stdout="ready", stderr="")

    test_context.patch.patch_object(hosted_docker_shell, "run_command", new=fake_run_command)
    clock = FakeClock()

    _wait_for_docker_ready(
        cwd=tmp_path,
        env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        deadline=PhaseDeadline.after(300, clock=clock),
        clock=clock,
    )

    assert attempts["count"] == 3


def test_wait_for_docker_ready_retries_after_binary_not_found(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Docker readiness probes should tolerate transient FileNotFoundError."""
    attempts = {"count": 0}

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "info"]
        assert cwd == tmp_path
        assert env == {"DOCKER_HOST": "unix:///tmp/docker.sock"}
        assert timeout_seconds == 30
        assert capture_output is True
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise FileNotFoundError(2, "No such file or directory", "docker")
        return subprocess.CompletedProcess(command, 0, stdout="ready", stderr="")

    test_context.patch.patch_object(hosted_docker_shell, "run_command", new=fake_run_command)
    clock = FakeClock()

    _wait_for_docker_ready(
        cwd=tmp_path,
        env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        deadline=PhaseDeadline.after(300, clock=clock),
        clock=clock,
    )

    assert attempts["count"] == 3


@_ORBSTACK_ONLY
def test_collect_runtime_diagnostics_uses_compose_probe_env(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Hosted runtime diagnostics should reuse compose probe env for compose commands."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.set("DOCKER_HOST", "unix:///tmp/orbstack-test.sock")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    local_env_file = varlock_local_env_file(
        workspace,
        home_dir=Path(context.app_home),
        environ=phase_env(context),
    )
    local_env_file.parent.mkdir(parents=True, exist_ok=True)
    write_env_assignments(
        local_env_file,
        {
            "NEO4J_PASSWORD": "runtime-secret",
        },
    )
    commands: list[tuple[list[str], dict[str, str]]] = []

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_seconds, capture_output
        commands.append((command, env))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    def fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
        del timeout_seconds
        return 4 if name == "hw.ncpu" else 8

    test_context.patch.patch_object(hosted_docker_diagnostics, "run_command", new=fake_run_command)
    test_context.patch.patch_object(
        hosted_docker_diagnostics,
        "sysctl_int",
        new=fake_sysctl_int,
    )

    hosted_docker.collect_runtime_diagnostics(Path(context.context_path))

    command_names = {" ".join(command) for command, _env in commands}
    assert "docker context ls" in command_names
    assert "orb status" in command_names
    assert "orb version" in command_names
    assert any(command.startswith("/usr/bin/log show") for command in command_names)
    assert "vm_stat" in command_names
    assert "df -h" in command_names
    compose_commands = [
        env for command, env in commands if command[:3] == ["docker", "compose", "-f"]
    ]
    assert compose_commands
    assert all(env["NEO4J_PASSWORD"] == "runtime-secret" for env in compose_commands)
    assert all("COMPOSE_PROJECT_NAME" in env for env in compose_commands)
    socket_state = (Path(context.diagnostics_dir) / "docker-socket-state.txt").read_text(
        encoding="utf-8"
    )
    assert "DOCKER_HOST=unix:///tmp/orbstack-test.sock" in socket_state
    assert "path=/tmp/orbstack-test.sock" in socket_state


def test_wait_runtime_ready_rejects_non_macos_context(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """wait_runtime_ready should reject Linux scenarios with FreshHostError."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    with pytest.raises(fresh_host.FreshHostError):
        hosted_docker_runtime.wait_runtime_ready(Path(context.context_path))


def test_setup_orbstack_rejects_checksum_mismatch(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """setup_orbstack should reject and remove a cached DMG with the wrong digest."""
    dmg_path = tmp_path / "orbstack.dmg"
    dmg_path.write_bytes(b"not the expected dmg")
    test_context.patch.patch_object(hosted_docker_runtime.sys, "platform", new="darwin")

    with pytest.raises(fresh_host.FreshHostError, match="checksum mismatch"):
        hosted_docker_runtime.setup_orbstack(
            dmg_path=dmg_path,
            dmg_url="https://example.invalid/orbstack.dmg",
            expected_sha256="0" * 64,
        )

    assert not dmg_path.exists()


@_ORBSTACK_ONLY
def test_wait_runtime_ready_sets_orbstack_socket_when_docker_host_unset(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """wait_runtime_ready must fall back to OrbStack's socket path when DOCKER_HOST is unset."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.remove("DOCKER_HOST", raising=False)

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    captured: list[dict[str, str]] = []

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        captured.append(
            {"DOCKER_HOST": env.get("DOCKER_HOST", ""), "window": str(deadline.remaining(clock))}
        )

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output  # noqa: ARG001
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
        del timeout_seconds
        return 4 if name == "hw.ncpu" else 8 * 1073741824

    test_context.patch.patch_object(
        hosted_docker_runtime, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )
    test_context.patch.patch_object(hosted_docker_runtime, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(hosted_docker_runtime, "sysctl_int", new=fake_sysctl_int)

    wait_github_env = tmp_path / "wait-github.env"
    hosted_docker_runtime.wait_runtime_ready(
        Path(context.context_path),
        github_env_file=wait_github_env,
        environ={},
        clock=FakeClock(),
    )

    assert len(captured) == 1
    expected_socket = Path.home() / ".orbstack" / "run" / "docker.sock"
    expected_docker_host = f"unix://{expected_socket}"
    assert captured[0]["DOCKER_HOST"] == expected_docker_host
    # Without a job budget the phase is its cap; the initial gate leaves the recovery reserve.
    assert float(captured[0]["window"]) == (
        RUNTIME_PHASE_CAP_SECONDS - RUNTIME_RECOVERY_RESERVE_SECONDS
    )
    # DOCKER_HOST must be exported to GITHUB_ENV so subsequent workflow steps
    # (run-scenario, cleanup) reach OrbStack's socket instead of the absent
    # default /var/run/docker.sock.
    written_env = dict(
        line.split("=", 1)
        for line in wait_github_env.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert written_env.get("DOCKER_HOST") == expected_docker_host


@_ORBSTACK_ONLY
def test_wait_runtime_ready_recovers_orbstack_with_stop_start_fallback(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """wait_runtime_ready should recover OrbStack when Docker reports socket EOF."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.set("DOCKER_HOST", "unix:///tmp/orbstack-test.sock")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    wait_calls = 0
    recovery_commands: list[list[str]] = []

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        nonlocal wait_calls
        del cwd, env
        wait_calls += 1
        if wait_calls == 1:
            raise fresh_host.FreshHostError("error during connect: EOF")
        assert deadline.remaining(clock) > 0

    def fake_runtime_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        recovery_commands.append(command)
        if command == ["orb", "restart", "docker"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="restart failed")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def fake_diagnostics_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
        del timeout_seconds
        return 4 if name == "hw.ncpu" else 8 * 1073741824

    test_context.patch.patch_object(
        hosted_docker_runtime, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )
    test_context.patch.patch_object(
        hosted_docker_runtime, "run_command", new=fake_runtime_run_command
    )
    test_context.patch.patch_object(hosted_docker_runtime, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(hosted_docker_runtime, "sysctl_int", new=fake_sysctl_int)
    test_context.patch.patch_object(
        hosted_docker_diagnostics, "run_command", new=fake_diagnostics_run_command
    )
    test_context.patch.patch_object(hosted_docker_diagnostics, "sysctl_int", new=fake_sysctl_int)

    report = hosted_docker_runtime.wait_runtime_ready(
        Path(context.context_path), environ={}, clock=FakeClock()
    )

    assert report.failure_reason is None
    assert report.failure_phase is None
    assert report.recovery_attempt_count == 1
    assert report.recovery_attempts[0].status == "success"
    assert report.recovery_attempts[0].trigger_reason == "docker_socket_eof"
    assert recovery_commands == [
        ["orb", "restart", "docker"],
        ["orb", "stop"],
        ["orb", "start"],
    ]
    assert (Path(context.diagnostics_dir) / "runtime-recovery/attempt-01/before").is_dir()
    assert (Path(context.diagnostics_dir) / "runtime-recovery/attempt-01/after").is_dir()
    payload = json.loads(Path(context.runtime_report_path or "").read_text(encoding="utf-8"))
    assert payload["recovery_attempt_count"] == 1
    assert payload["recovery_attempts"][0]["recovery_exit_code"] == 0


@_ORBSTACK_ONLY
def test_wait_runtime_ready_reports_exhausted_orbstack_recovery(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """wait_runtime_ready should fail with structured recovery metadata."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.set("DOCKER_HOST", "unix:///tmp/orbstack-test.sock")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env, deadline, clock
        raise fresh_host.FreshHostError("error during connect: EOF")

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
        del timeout_seconds
        return 4 if name == "hw.ncpu" else 8 * 1073741824

    test_context.patch.patch_object(
        hosted_docker_runtime, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )
    test_context.patch.patch_object(hosted_docker_runtime, "run_command", new=fake_run_command)
    test_context.patch.patch_object(hosted_docker_runtime, "sysctl_int", new=fake_sysctl_int)
    test_context.patch.patch_object(hosted_docker_diagnostics, "run_command", new=fake_run_command)
    test_context.patch.patch_object(hosted_docker_diagnostics, "sysctl_int", new=fake_sysctl_int)

    # Fast failures leave time on the clock, so this is a recovery failure, not exhaustion.
    with pytest.raises(
        fresh_host.FreshHostError,
        match="orbstack_recovery_failed: docker_socket_eof",
    ):
        hosted_docker_runtime.wait_runtime_ready(
            Path(context.context_path), environ={}, clock=FakeClock()
        )

    payload = json.loads(Path(context.runtime_report_path or "").read_text(encoding="utf-8"))
    assert payload["failure_reason"] == "orbstack_recovery_failed"
    assert payload["failure_phase"] == "post_recovery_probe"
    assert payload["last_failure_reason"] == "docker_socket_eof"
    assert payload["deadline_limited_by"] == "phase-cap"
    assert payload["recovery_attempt_count"] == 2
    assert [attempt["status"] for attempt in payload["recovery_attempts"]] == [
        "failure",
        "failure",
    ]
    assert payload["recovery_attempts"][-1]["readiness_reason"] == "docker_socket_eof"


def test_run_checked_handles_none_outputs_when_capture_output_is_false(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """run_checked must not crash with AttributeError when capture_output=False.

    subprocess.run with capture_output=False leaves stdout and stderr as None on the
    returned CompletedProcess.  The error-path in run_checked must guard against this
    via ``(x or "").strip()`` rather than ``x.strip()``.
    """

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        return subprocess.CompletedProcess(command, returncode=1, stdout=None, stderr=None)

    test_context.patch.patch_object(hosted_docker_shell, "run_command", new=fake_run_command)

    with pytest.raises(fresh_host.FreshHostError, match="command failed"):
        hosted_docker_shell.run_checked(["false"], cwd=tmp_path, env={}, capture_output=False)


def _budgeted_deadline(clock: FakeClock, seconds: int) -> PhaseDeadline:
    return PhaseDeadline.after(seconds, clock=clock)


def test_pull_images_clips_attempt_timeout_to_phase_deadline(
    test_context: TestContext,
) -> None:
    """No single pull may outlive the phase deadline."""
    clock = FakeClock()
    timeouts: list[int] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        timeouts.append(timeout_seconds)
        return image, 0, 0.1, ""

    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=1,
        max_attempts=5,
        deadline=_budgeted_deadline(clock, 400),
        clock=clock,
        client=_FunctionPullClient(fake_pull_one_image),
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert timeouts == [400]


def test_pull_images_stops_when_budget_cannot_fit_another_attempt(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A slow failing pull must end in budget_exhausted instead of a fresh attempt."""
    clock = FakeClock()
    attempts: list[str] = []
    probes: list[float] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        attempts.append(image)
        clock.advance(timeout_seconds - 30)
        return image, 1, float(timeout_seconds), "docker pull timed out"

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env
        probes.append(deadline.remaining(clock))

    test_context.patch.patch_object(
        hosted_docker_images, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )
    events = PullEventLog(tmp_path / "events.jsonl")

    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=1,
        max_attempts=5,
        recovery_cwd=tmp_path,
        recovery_env={},
        deadline=_budgeted_deadline(clock, 600),
        clock=clock,
        event_log=events,
        client=_FunctionPullClient(fake_pull_one_image),
        jitter=_no_jitter,
    )

    assert report.exit_code == 1
    assert report.failure_class == "budget_exhausted"
    assert attempts == ["postgres:16"]
    assert probes == []
    assert clock.sleeps == []
    assert [event["event"] for event in read_events(events.path)] == [
        "attempt_started",
        "image_result",
        "budget_exhausted",
    ]


def test_pull_images_skips_backoff_that_would_breach_budget(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Backoff and recovery probes are clipped to what the remaining budget allows."""
    clock = FakeClock()
    probes: list[float] = []

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        del timeout_seconds
        return image, 1, 1.0, "context deadline exceeded"

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env
        probes.append(deadline.remaining(clock))
        cast(FakeClock, clock).advance(40)

    test_context.patch.patch_object(
        hosted_docker_images, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )

    report = hosted_docker.pull_images(
        ["qdrant/qdrant:v1"],
        parallelism=1,
        max_attempts=5,
        recovery_cwd=tmp_path,
        recovery_env={},
        deadline=_budgeted_deadline(clock, 105),
        clock=clock,
        client=_FunctionPullClient(fake_pull_one_image),
        jitter=_no_jitter,
    )

    # 105s budget: the readiness wait gets 45s, keeping one 60s attempt window in reserve.
    # It spends 40s, so the 10s backoff would leave less than that window: stop, no sleep.
    assert report.failure_class == "budget_exhausted"
    assert probes == [45]
    assert clock.sleeps == []


def test_pull_images_restarts_runtime_on_daemon_failure_when_hook_given(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A dead daemon triggers runtime recovery (OrbStack restart), not only a readiness wait."""
    calls: list[str] = []
    attempts = {"count": 0}

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        del timeout_seconds
        attempts["count"] += 1
        if attempts["count"] == 1:
            return image, 1, 0.1, "Cannot connect to the Docker daemon at unix:///x.sock"
        return image, 0, 0.1, ""

    def fake_wait_for_docker_ready(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        del cwd, env, deadline, clock
        calls.append("wait")

    test_context.patch.patch_object(
        hosted_docker_images, "wait_for_docker_ready", new=fake_wait_for_docker_ready
    )

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=1,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={},
        recover_runtime=lambda deadline: calls.append("restart"),
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert calls == ["restart"]


def test_pull_images_reports_daemon_unavailable_when_recovery_fails(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        del timeout_seconds
        return image, 1, 0.1, "error during connect: EOF"

    def failed_recovery(deadline: PhaseDeadline) -> None:
        del deadline
        raise fresh_host.FreshHostError("orbstack_recovery_failed")

    clock = FakeClock()
    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=1,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={},
        recover_runtime=failed_recovery,
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 1
    assert report.failure_class == "daemon_unavailable"


def _linux_context(tmp_path: Path, test_context: TestContext) -> fresh_host.FreshHostContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    return fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=tmp_path / "github.env",
    )


def test_ensure_images_fails_fast_and_reports_when_budget_is_exhausted(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Reaching the scenario/observer reserve must fail before pulling, with evidence."""
    context = _linux_context(tmp_path, test_context)
    clock = FakeClock()
    budget = initialize_job_budget(
        job_timeout_minutes=75,
        scenario_reserve_minutes=20,
        observer_reserve_minutes=9,
        environ={},
        clock=clock,
    )
    clock.advance(60 * 60)

    with pytest.raises(fresh_host.FreshHostError, match="budget_exhausted"):
        hosted_docker.ensure_images(
            Path(context.context_path), environ=budget.to_env(), clock=clock
        )

    assert context.image_report_path is not None
    payload = json.loads(Path(context.image_report_path).read_text(encoding="utf-8"))
    assert payload["failure_class"] == "budget_exhausted"
    assert payload["pull_attempt_count"] == 0


def test_ensure_images_writes_report_when_resolution_fails(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Any pre-pull failure must still leave an ensure report and phase event."""
    context = _linux_context(tmp_path, test_context)

    def failing_run_checked(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise fresh_host.FreshHostError("compose config failed")

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=failing_run_checked)

    with pytest.raises(fresh_host.FreshHostError, match="compose config failed"):
        hosted_docker.ensure_images(Path(context.context_path), environ={})

    assert context.image_report_path is not None
    payload = json.loads(Path(context.image_report_path).read_text(encoding="utf-8"))
    assert payload["failure_reason"] == "compose config failed"
    assert payload["failure_class"] == "ensure_failed"
    assert payload["pull_deadline_limited_by"] == "phase-cap"
    events = read_events(Path(cast(str, payload["pull_events_path"])))
    assert [event["event"] for event in events] == ["phase_started"]


_GIB = 1073741824


def _macos_context(tmp_path: Path, test_context: TestContext) -> fresh_host.FreshHostContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    test_context.env.set("DOCKER_HOST", "unix:///tmp/orbstack-test.sock")
    return fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=tmp_path / "github.env",
    )


def _macos_budget(clock: FakeClock) -> JobBudget:
    return initialize_job_budget(
        job_timeout_minutes=90,
        scenario_reserve_minutes=35,
        observer_reserve_minutes=15,
        environ={},
        clock=clock,
    )


def _fake_sysctl_int(name: str, *, timeout_seconds: float = 30) -> int | None:
    del timeout_seconds
    return 4 if name == "hw.ncpu" else 8 * _GIB


def _patch_runtime_commands(
    test_context: TestContext,
    *,
    wait: _WaitForDockerReady,
) -> list[tuple[list[str], float]]:
    """Fake every runtime and diagnostics command as instant success; return what ran."""
    commands: list[tuple[list[str], float]] = []

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output
        commands.append((list(command), timeout_seconds))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    for module in (hosted_docker_runtime, hosted_docker_diagnostics):
        test_context.patch.patch_object(module, "run_command", new=fake_run_command)
        test_context.patch.patch_object(module, "sysctl_int", new=_fake_sysctl_int)
    test_context.patch.patch_object(hosted_docker_runtime, "run_checked", new=fake_run_command)
    test_context.patch.patch_object(hosted_docker_runtime, "wait_for_docker_ready", new=wait)
    return commands


def _hang_until_deadline(
    *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
) -> float:
    """Spend a readiness window to its end; return how long it was."""
    del cwd, env
    window = deadline.remaining(clock)
    cast(FakeClock, clock).advance(window)
    return window


def test_wait_for_docker_ready_clips_a_hung_probe_to_the_deadline(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A probe that hangs for its full timeout cannot push the wait past its deadline."""
    clock = FakeClock()
    deadline = PhaseDeadline.after(45, clock=clock)
    timeouts: list[float] = []

    def hung_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output
        timeouts.append(timeout_seconds)
        clock.advance(timeout_seconds)
        raise subprocess.TimeoutExpired(command, timeout_seconds)

    test_context.patch.patch_object(hosted_docker_shell, "run_command", new=hung_run_command)

    with pytest.raises(fresh_host.FreshHostError, match="did not become ready.*timed out"):
        _wait_for_docker_ready(cwd=tmp_path, env={}, deadline=deadline, clock=clock)

    # 30s probe, 2s poll gap, then the last probe gets only the 13s that remain.
    assert timeouts == [30, 13]
    assert clock.sleeps == [2, 0]
    assert deadline.remaining(clock) == 0.0


def test_wait_runtime_ready_always_runs_recovery_inside_its_reserved_slice(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A gate that hangs to its deadline still leaves recovery its slice, then budget_exhausted."""
    context = _macos_context(tmp_path, test_context)
    clock = FakeClock()
    budget = _macos_budget(clock)
    # 300s into the job, the runtime phase is 2280 - 300 - 1440 = 540s (job-reserve bound).
    clock.advance(300)
    phase_start = clock.monotonic()
    windows: list[float] = []

    def hung_wait(*, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock) -> None:
        windows.append(_hang_until_deadline(cwd=cwd, env=env, deadline=deadline, clock=clock))
        raise fresh_host.FreshHostError(
            "docker did not become ready before the deadline; last: error during connect: EOF"
        )

    commands = _patch_runtime_commands(test_context, wait=hung_wait)

    with pytest.raises(BudgetExhaustedError, match="^budget_exhausted: .*docker_socket_eof"):
        hosted_docker_runtime.wait_runtime_ready(
            Path(context.context_path), environ=budget.to_env(), clock=clock
        )

    # Initial gate: 540 - 360 reserved. Attempt 1: 180s slice minus the 30s after-diagnostics
    # slice and the 5s backoff. Attempt 2 inherits the rest, minus 30s and its 15s backoff.
    assert windows == pytest.approx([180, 145, 165])
    assert clock.monotonic() - phase_start <= 540
    orb_commands = [command for command, _ in commands if command[0] == "orb"]
    assert orb_commands.count(["orb", "restart", "docker"]) == 2
    payload = json.loads(Path(context.runtime_report_path or "").read_text(encoding="utf-8"))
    assert payload["failure_reason"] == "budget_exhausted"
    assert payload["last_failure_reason"] == "docker_socket_eof"
    assert payload["failure_phase"] == "post_recovery_probe"
    assert payload["deadline_limited_by"] == "job-reserve"
    assert payload["recovery_attempt_count"] == 2
    main_report = json.loads(Path(context.report_path).read_text(encoding="utf-8"))
    assert main_report["failure_reason"] == "budget_exhausted"


def test_wait_runtime_ready_writes_budget_exhausted_report_when_no_time_remains(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A runtime phase that starts inside the later reserves fails fast with evidence."""
    context = _macos_context(tmp_path, test_context)
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(15 * 60)

    def unexpected_wait(
        *, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock
    ) -> None:
        raise AssertionError("no probe may start once the phase has no time")

    _patch_runtime_commands(test_context, wait=unexpected_wait)

    with pytest.raises(BudgetExhaustedError, match="^budget_exhausted: no runtime time"):
        hosted_docker_runtime.wait_runtime_ready(
            Path(context.context_path), environ=budget.to_env(), clock=clock
        )

    payload = json.loads(Path(context.runtime_report_path or "").read_text(encoding="utf-8"))
    assert payload["failure_reason"] == "budget_exhausted"
    assert payload["failure_phase"] == "phase_start"
    assert payload["deadline_limited_by"] is None


def test_wait_runtime_ready_scales_its_slices_and_clips_recovery_commands(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A phase below its minimum shrinks both slices; recovery commands fit their attempt."""
    context = _macos_context(tmp_path, test_context)
    clock = FakeClock()
    windows: list[float] = []

    def hung_wait(*, cwd: Path, env: dict[str, str], deadline: PhaseDeadline, clock: Clock) -> None:
        windows.append(_hang_until_deadline(cwd=cwd, env=env, deadline=deadline, clock=clock))
        raise fresh_host.FreshHostError("docker did not become ready; last: EOF")

    commands = _patch_runtime_commands(test_context, wait=hung_wait)

    with pytest.raises(BudgetExhaustedError):
        hosted_docker_runtime.wait_runtime_ready(
            Path(context.context_path),
            deadline=PhaseDeadline.after(210, clock=clock),
            clock=clock,
        )

    # 210s is half the 420s minimum: the initial gate keeps 30s and recovery 180s, split
    # into a 90s and a 120s attempt (the first one's unused diagnostics slice carries over).
    assert windows == pytest.approx([30, 55, 75])
    orb_timeouts = [
        timeout
        for command, timeout in commands
        if command[0] == "orb" and command[1] in {"restart", "stop", "start"}
    ]
    assert orb_timeouts == pytest.approx([60, 90])


def test_collect_runtime_diagnostics_stays_inside_its_slice(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Slow diagnostics consume only their slice; the rest are recorded as skipped."""
    context = _macos_context(tmp_path, test_context)
    clock = FakeClock()
    deadline = PhaseDeadline.after(30, clock=clock)
    diagnostics_dir = tmp_path / "diagnostics"
    timeouts: list[float] = []

    def slow_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output
        timeouts.append(timeout_seconds)
        clock.advance(min(12, timeout_seconds))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    test_context.patch.patch_object(hosted_docker_diagnostics, "run_command", new=slow_run_command)
    test_context.patch.patch_object(hosted_docker_diagnostics, "sysctl_int", new=_fake_sysctl_int)

    hosted_docker_diagnostics.collect_runtime_diagnostics_for_context(
        context,
        diagnostics_dir,
        deadline=deadline,
        env={"DOCKER_HOST": "unix:///tmp/orbstack-test.sock"},
        clock=clock,
    )

    assert timeouts == [30, 18, 6]
    assert deadline.remaining(clock) == 0.0
    assert (diagnostics_dir / "orb-status.txt").read_text(encoding="utf-8") == "ok\n"
    assert (diagnostics_dir / "docker-socket-state.txt").is_file()
    assert (diagnostics_dir / "host-cpu-count.txt").read_text(encoding="utf-8") == "4\n"
    skipped = (diagnostics_dir / "orbstack-unified.log").read_text(encoding="utf-8")
    assert skipped.startswith("skipped: budget_exhausted")


def test_pull_images_hands_runtime_recovery_a_slice_of_the_image_deadline(
    tmp_path: Path,
) -> None:
    """Runtime recovery inside the image phase is bounded by the image deadline."""
    clock = FakeClock()
    deadline = _budgeted_deadline(clock, 300)
    received: list[tuple[PhaseDeadline, float]] = []
    attempts = {"count": 0}

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        del timeout_seconds
        attempts["count"] += 1
        if attempts["count"] == 1:
            return image, 1, 0.1, "Cannot connect to the Docker daemon at unix:///x.sock"
        return image, 0, 0.1, ""

    def recover(recovery_deadline: PhaseDeadline) -> None:
        received.append((recovery_deadline, recovery_deadline.remaining(clock)))

    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=1,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={},
        deadline=deadline,
        recover_runtime=recover,
        client=_FunctionPullClient(fake_pull_one_image),
        clock=clock,
        jitter=_no_jitter,
    )

    assert report.exit_code == 0
    assert len(received) == 1
    # The slice keeps one 60s pull attempt in reserve after recovery.
    recovery_deadline, remaining_at_recovery = received[0]
    assert remaining_at_recovery == pytest.approx(240)
    assert recovery_deadline.monotonic_deadline <= deadline.monotonic_deadline - 60


def test_ensure_images_passes_its_deadline_into_macos_runtime_recovery(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """The macOS recovery hook restarts the runtime inside the slice it is handed."""
    context = _macos_context(tmp_path, test_context)
    clock = FakeClock()
    captured: dict[str, object] = {}

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output
        captured["resolution_timeout"] = timeout_seconds
        return subprocess.CompletedProcess(command, 0, stdout="postgres:16\n", stderr="")

    listings = {"count": 0}

    def fake_list_local_images(images: list[str], **kwargs: object) -> list[str]:
        captured.setdefault("list_deadline", kwargs.get("deadline"))
        listings["count"] += 1
        return [] if listings["count"] == 1 else list(images)

    def fake_pull_images(images: list[str], **kwargs: object) -> hosted_docker.PullReport:
        captured["pull_deadline"] = kwargs["deadline"]
        captured["recover_runtime"] = kwargs["recover_runtime"]
        return hosted_docker.PullReport(
            exit_code=0,
            pulled_images=list(images),
            failed_images=[],
            attempt_count=1,
            retried_images=[],
        )

    def fake_wait_runtime_ready(context_path: Path, **kwargs: object) -> None:
        del context_path
        captured["runtime_kwargs"] = kwargs

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(
        hosted_docker_images, "list_local_images", new=fake_list_local_images
    )
    test_context.patch.patch_object(hosted_docker_images, "pull_images", new=fake_pull_images)
    test_context.patch.patch_object(
        hosted_docker_images, "wait_runtime_ready", new=fake_wait_runtime_ready
    )

    hosted_docker.ensure_images(Path(context.context_path), environ={}, clock=clock)

    image_deadline = cast(PhaseDeadline, captured["pull_deadline"])
    assert captured["list_deadline"] is image_deadline
    assert captured["resolution_timeout"] == 120
    recover = cast(hosted_docker_images.RuntimeRecovery, captured["recover_runtime"])
    recovery_slice = image_deadline.slice(clock, cap_seconds=100)
    recover(recovery_slice)
    assert captured["runtime_kwargs"] == {"deadline": recovery_slice, "clock": clock}


def test_ensure_images_clips_compose_resolution_to_the_image_deadline(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Pre-pull commands share the image deadline instead of fixed per-command timeouts."""
    context = _linux_context(tmp_path, test_context)
    clock = FakeClock()
    budget = initialize_job_budget(
        job_timeout_minutes=75,
        scenario_reserve_minutes=20,
        observer_reserve_minutes=9,
        environ={},
        clock=clock,
    )
    # 75m job: the image deadline is (4500 - 120) - (20 + 9) * 60 = 2640s in; leave it 50s.
    clock.advance(2640 - 50)
    timeouts: list[float] = []

    def hung_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del command, cwd, env, capture_output
        timeouts.append(timeout_seconds)
        clock.advance(timeout_seconds)
        raise fresh_host.FreshHostError("docker compose config hung")

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=hung_run_checked)

    with pytest.raises(fresh_host.FreshHostError, match="hung"):
        hosted_docker.ensure_images(
            Path(context.context_path), environ=budget.to_env(), clock=clock
        )

    assert timeouts == [pytest.approx(50)]


def _process_is_gone(pid: int) -> bool:
    """Return whether *pid* exited; a zombie awaiting its reaper counts as gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return not state or state.startswith("Z")


def test_run_command_kills_the_whole_process_group_on_timeout(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """A timed-out command and a TERM-ignoring grandchild both die with the group."""
    test_context.patch.patch_object(hosted_docker_process, "TERMINATE_GRACE_SECONDS", new=0.2)
    sleeper = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    script = (
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"child = subprocess.Popen([sys.executable, '-c', {sleeper!r}])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        hosted_docker_shell.run_command(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_seconds=1.5,
            capture_output=True,
        )

    assert time.monotonic() - started < 10
    grandchild = int(str(excinfo.value.output).split()[0])
    give_up = time.monotonic() + 5
    while not _process_is_gone(grandchild) and time.monotonic() < give_up:
        time.sleep(0.05)
    assert _process_is_gone(grandchild)
