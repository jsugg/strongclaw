"""Unit coverage for hosted Docker CI helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.utils.helpers import fresh_host, hosted_docker
from tests.utils.helpers._hosted_docker import images as hosted_docker_images
from tests.utils.helpers._hosted_docker import shell as hosted_docker_shell


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
    monkeypatch.setattr(hosted_docker_images.time, "sleep", lambda _: None)

    report = hosted_docker.pull_images(
        ["postgres:16", "qdrant:v1"],
        parallelism=4,
        max_attempts=3,
    )

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert report.retried_images == ["postgres:16"]
    assert set(report.pulled_images) == {"postgres:16", "qdrant:v1"}


def test_pull_one_image_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timed-out pulls should return a structured failure result."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["docker", "pull", "postgres:16"], timeout=42)

    monkeypatch.setattr(hosted_docker_images.subprocess, "run", fake_run)

    image, returncode, _, output = hosted_docker._pull_one_image("postgres:16", 42)

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
    monkeypatch.setattr(hosted_docker_shell.time, "sleep", lambda _: None)

    hosted_docker._wait_for_docker_ready(
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
