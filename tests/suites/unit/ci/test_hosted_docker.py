"""Unit coverage for hosted Docker CI helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Protocol, cast

import pytest

from clawops.strongclaw_runtime import varlock_local_env_file, write_env_assignments
from tests.utils.helpers import fresh_host, hosted_docker
from tests.utils.helpers._fresh_host.shell import phase_env
from tests.utils.helpers._hosted_docker import diagnostics as hosted_docker_diagnostics
from tests.utils.helpers._hosted_docker import images as hosted_docker_images
from tests.utils.helpers._hosted_docker import shell as hosted_docker_shell


class _PullOneImage(Protocol):
    def __call__(self, image: str, timeout_seconds: int) -> tuple[str, int, float, str]: ...


class _WaitForDockerReady(Protocol):
    def __call__(self, *, cwd: Path, env: dict[str, str], max_attempts: int = 60) -> None: ...


_pull_one_image = cast(
    _PullOneImage,
    cast(Any, hosted_docker)._pull_one_image,
)
_wait_for_docker_ready = cast(
    _WaitForDockerReady,
    cast(Any, hosted_docker)._wait_for_docker_ready,
)


def _sleep(_: float) -> None:
    return None


def test_pull_images_retries_with_reduced_parallelism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image pulls should retry failures with reduced parallelism."""
    attempts: dict[str, int] = {"postgres:16": 0, "qdrant:v1": 0}

    def fake_pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
        assert timeout_seconds == hosted_docker.DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS
        attempts[image] += 1
        if image == "postgres:16" and attempts[image] == 1:
            return image, 1, 0.1, "unexpected EOF"
        return image, 0, 0.1, ""

    monkeypatch.setattr(hosted_docker_images, "pull_one_image", fake_pull_one_image)

    monkeypatch.setattr(hosted_docker_images.time, "sleep", _sleep)

    report = hosted_docker.pull_images(
        ["postgres:16", "qdrant:v1"],
        parallelism=4,
        max_attempts=3,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert report.retried_images == ["postgres:16"]
    assert set(report.pulled_images) == {"postgres:16", "qdrant:v1"}


def test_pull_images_waits_for_daemon_recovery_on_connectivity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image pulls should probe daemon recovery after connectivity failures."""
    attempts: dict[str, int] = {"postgres:16": 0}
    recovery_probes: list[tuple[Path, dict[str, str], int]] = []

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
        *,
        cwd: Path,
        env: dict[str, str],
        max_attempts: int = 60,
    ) -> None:
        recovery_probes.append((cwd, dict(env), max_attempts))

    monkeypatch.setattr(hosted_docker_images, "pull_one_image", fake_pull_one_image)
    monkeypatch.setattr(hosted_docker_images, "wait_for_docker_ready", fake_wait_for_docker_ready)
    monkeypatch.setattr(hosted_docker_images.time, "sleep", _sleep)

    report = hosted_docker.pull_images(
        ["postgres:16"],
        parallelism=2,
        max_attempts=3,
        recovery_cwd=tmp_path,
        recovery_env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert recovery_probes == [(tmp_path, {"DOCKER_HOST": "unix:///tmp/docker.sock"}, 90)]


def test_pull_images_requires_recovery_cwd_and_env_together() -> None:
    """Pull recovery wiring should reject partial recovery configuration."""
    with pytest.raises(fresh_host.FreshHostError, match="recovery_cwd and recovery_env"):
        hosted_docker.pull_images(
            ["postgres:16"],
            parallelism=1,
            max_attempts=1,
            recovery_cwd=Path("/tmp"),
        )


def test_pull_one_image_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timed-out pulls should return a structured failure result."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["docker", "pull", "postgres:16"], timeout=42)

    monkeypatch.setattr(hosted_docker_images.subprocess, "run", fake_run)

    image, returncode, _, output = _pull_one_image("postgres:16", 42)

    assert image == "postgres:16"
    assert returncode == 1
    assert output == "docker pull timed out after 42s"


def test_resolve_compose_images_uses_first_seen_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(hosted_docker_images, "run_checked", fake_run_checked)

    images = hosted_docker.resolve_compose_images(
        [compose_a, compose_b],
        cwd=tmp_path,
        env={"STRONGCLAW_COMPOSE_STATE_DIR": str(tmp_path / "state")},
    )

    assert images == ["postgres:16", "qdrant:v1", "browserlab:latest"]


def test_ensure_images_noops_when_context_disables_image_warming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image ensure should no-op for scenarios that do not warm images."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == []
    assert report.pull_attempt_count == 0
    assert context.image_report_path is None


def test_ensure_images_inherits_repo_local_varlock_assignments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image resolution should honor repo-local Varlock compose secrets."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")

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

    def fake_list_local_images(images: list[str]) -> list[str]:
        return list(images)

    monkeypatch.setattr(hosted_docker_images, "run_checked", fake_run_checked)
    monkeypatch.setattr(hosted_docker_images, "list_local_images", fake_list_local_images)

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == ["postgres:16"]
    assert report.missing_before_pull == []
    assert report.pull_attempt_count == 0


def test_ensure_images_uses_compose_resolution_placeholders_before_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image resolution should prefill required compose secrets before setup runs."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("VARLOCK_LOCAL_ENV_FILE", str(tmp_path / "missing.env"))

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

    def fake_list_local_images(images: list[str]) -> list[str]:
        return list(images)

    monkeypatch.setattr(hosted_docker_images, "run_checked", fake_run_checked)
    monkeypatch.setattr(hosted_docker_images, "list_local_images", fake_list_local_images)

    report = hosted_docker.ensure_images(Path(context.context_path))

    assert report.images == ["postgres:16"]
    assert report.missing_before_pull == []
    assert report.pull_attempt_count == 0


def test_wait_for_docker_ready_retries_after_probe_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(hosted_docker_shell, "run_command", fake_run_command)
    monkeypatch.setattr(hosted_docker_shell.time, "sleep", _sleep)

    _wait_for_docker_ready(
        cwd=tmp_path,
        env={"DOCKER_HOST": "unix:///tmp/docker.sock"},
        max_attempts=4,
    )

    assert attempts["count"] == 3


def test_install_runtime_rejects_non_macos_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted runtime installation should reject Linux scenarios."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    with pytest.raises(fresh_host.FreshHostError):
        hosted_docker.install_runtime(Path(context.context_path))


def test_collect_runtime_diagnostics_uses_compose_probe_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted runtime diagnostics should reuse compose probe env for compose commands."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")

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

    def fake_sysctl_int(name: str) -> int | None:
        return 4 if name == "hw.ncpu" else 8

    monkeypatch.setattr(hosted_docker_diagnostics, "run_command", fake_run_command)
    monkeypatch.setattr(
        hosted_docker_diagnostics,
        "sysctl_int",
        fake_sysctl_int,
    )

    hosted_docker.collect_runtime_diagnostics(Path(context.context_path))

    compose_commands = [
        env for command, env in commands if command[:3] == ["docker", "compose", "-f"]
    ]
    assert compose_commands
    assert all(env["NEO4J_PASSWORD"] == "runtime-secret" for env in compose_commands)
    assert all("COMPOSE_PROJECT_NAME" in env for env in compose_commands)
