"""Unit coverage for fresh-host CI helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawops.strongclaw_compose import compose_project_name
from clawops.strongclaw_runtime import resolve_repo_local_compose_state_dir
from tests.scripts import fresh_host as fresh_host_script
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import linux as fresh_host_linux
from tests.utils.helpers._fresh_host import macos as fresh_host_macos
from tests.utils.helpers._fresh_host import scenario as fresh_host_scenario
from tests.utils.helpers._fresh_host import shell as fresh_host_shell


def test_prepare_context_writes_context_and_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Context preparation should persist files and downstream env exports."""
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

    assert Path(context.context_path).is_file()
    assert Path(context.report_path).is_file()
    exports = github_env.read_text(encoding="utf-8")
    assert f"FRESH_HOST_CONTEXT={context.context_path}" in exports
    assert f"STRONGCLAW_APP_HOME={context.app_home}" in exports
    assert context.runtime_provider == "docker"


def test_scenario_phase_names_match_macos_browser_lab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browser-lab scenarios should skip machine-name and launchd phases."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-browser-lab",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    assert fresh_host.scenario_phase_names(context) == [
        "bootstrap",
        "setup",
        "exercise-browser-lab",
    ]
    assert context.activate_services is False
    assert context.compose_variant == "ci-hosted-macos"


def test_scenario_phase_names_deactivate_host_services_before_repo_local_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sidecars scenarios should tear down host services before repo-local exercise."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    assert fresh_host.scenario_phase_names(context) == [
        "normalize-machine-name",
        "bootstrap",
        "setup",
        "verify-launchd",
        "deactivate-services",
        "exercise-sidecars",
    ]


def test_prepare_context_sets_macos_variant_and_primary_compose_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sidecars scenarios should export the hosted macOS compose variant details."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    exports = github_env.read_text(encoding="utf-8")
    assert context.compose_variant == "ci-hosted-macos"
    assert context.compose_files == [
        str(
            (workspace / "platform/compose/docker-compose.aux-stack.ci-hosted-macos.yaml").resolve()
        )
    ]
    assert "STRONGCLAW_COMPOSE_VARIANT=ci-hosted-macos" in exports
    assert f"FRESH_HOST_PRIMARY_COMPOSE_FILE={context.compose_files[0]}" in exports


def test_fresh_host_cli_accepts_current_macos_scenarios() -> None:
    """The executable CLI should accept both current macOS scenario ids."""
    sidecars = fresh_host_script._parse_args(
        [
            "prepare-context",
            "--scenario",
            "macos-sidecars",
            "--runner-temp",
            "/tmp/runner",
        ]
    )
    browser_lab = fresh_host_script._parse_args(
        [
            "prepare-context",
            "--scenario",
            "macos-browser-lab",
            "--runner-temp",
            "/tmp/runner",
        ]
    )

    assert sidecars.scenario == "macos-sidecars"
    assert browser_lab.scenario == "macos-browser-lab"


def test_load_context_rejects_directory_path(tmp_path: Path) -> None:
    """Loading a context should fail cleanly when the path is not a JSON file."""
    with pytest.raises(fresh_host.FreshHostError, match="expected JSON file"):
        fresh_host.load_context(tmp_path)


def test_run_named_phase_supports_macos_service_deactivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sidecars macOS scenario should dispatch the deactivation phase."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    monkeypatch.setattr(
        fresh_host_macos,
        "deactivate_macos_host_services",
        lambda loaded_context: ["echo", loaded_context.scenario_id, "deactivate-services"],
    )

    assert fresh_host._run_named_phase(context, "deactivate-services") == [
        "echo",
        "macos-sidecars",
        "deactivate-services",
    ]


def test_run_scenario_records_successful_phase_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario execution should append one successful phase result per planned phase."""
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

    calls: list[str] = []

    def fake_run_named_phase(
        loaded_context: fresh_host.FreshHostContext,
        phase_name: str,
    ) -> list[str]:
        calls.append(phase_name)
        assert loaded_context.scenario_id == "linux"
        return ["echo", phase_name]

    monkeypatch.setattr(fresh_host_scenario, "run_named_phase", fake_run_named_phase)

    report = fresh_host.run_scenario(Path(context.context_path))

    assert report.status == "success"
    assert calls == fresh_host.scenario_phase_names(context)
    assert [phase.name for phase in report.phases] == calls
    assert all(phase.status == "success" for phase in report.phases)


def test_venv_clawops_command_preserves_virtualenv_entrypoint_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed commands should execute through the venv path, not the symlink target."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    venv_bin = workspace / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    target_python = tmp_path / "python-target"
    target_python.write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "python").symlink_to(target_python)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    command = fresh_host._venv_clawops_command(context, "setup")

    assert command[0] == str(venv_bin / "python")
    assert command[0] != str(target_python.resolve())


def test_wait_for_docker_backend_retries_after_transient_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient Linux docker probe failures should be retried before giving up."""
    attempts = {"count": 0}

    def fake_run(*args: object, **kwargs: object) -> object:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)
    monkeypatch.setattr(fresh_host_shell.time, "sleep", lambda _: None)

    fresh_host_shell.wait_for_docker_backend(cwd=tmp_path, env={"PATH": "/usr/bin"})

    assert attempts["count"] == 3


def test_verify_compose_services_running_accepts_json_lines_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose runtime verification should accept JSON-lines output from Docker."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    payload = "\n".join(
        [
            json.dumps({"Service": "browserlab-proxy", "State": "running"}),
            json.dumps({"Service": "browserlab-playwright", "State": "running"}),
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return type("Result", (), {"returncode": 0, "stdout": payload, "stderr": ""})()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )


def test_verify_compose_services_running_requires_healthy_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose runtime verification should reject unhealthy services."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payload = json.dumps(
        [
            {"Service": "postgres", "State": "running", "Health": "unhealthy"},
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return type("Result", (), {"returncode": 0, "stdout": payload, "stderr": ""})()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)

    with pytest.raises(fresh_host.FreshHostError, match="health 'unhealthy'"):
        fresh_host_shell.verify_compose_services_running(
            compose_file,
            cwd=tmp_path,
            env={"PATH": "/usr/bin"},
            expected_services=("postgres",),
            healthy_services=("postgres",),
            timeout_seconds=0,
        )


def test_verify_compose_services_running_retries_when_compose_ps_is_temporarily_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose runtime verification should retry through transient empty ps output."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payloads = iter(
        (
            "",
            json.dumps(
                [
                    {"Service": "browserlab-proxy", "State": "running"},
                    {"Service": "browserlab-playwright", "State": "running"},
                ]
            ),
        )
    )
    sleeps: list[float] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": next(payloads), "stderr": ""},
        )()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)
    monkeypatch.setattr(fresh_host_shell.time, "sleep", sleeps.append)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )

    assert sleeps == [2.0]


def test_verify_compose_services_running_retries_until_services_are_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose runtime verification should tolerate short startup races."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payloads = iter(
        (
            json.dumps([{"Service": "browserlab-proxy", "State": "running"}]),
            json.dumps(
                [
                    {"Service": "browserlab-proxy", "State": "running"},
                    {"Service": "browserlab-playwright", "State": "running"},
                ]
            ),
        )
    )
    sleeps: list[float] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": next(payloads), "stderr": ""},
        )()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)
    monkeypatch.setattr(fresh_host_shell.time, "sleep", sleeps.append)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )

    assert sleeps == [2.0]


def test_verify_compose_services_running_uses_scenario_home_for_compose_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose runtime probes should inherit state/config paths from the scenario home."""
    repo_root = tmp_path / "repo"
    compose_dir = repo_root / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    compose_file = compose_dir / "docker-compose.browser-lab.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    captured_env: dict[str, str] = {}
    payload = json.dumps(
        [
            {"Service": "browserlab-proxy", "State": "running"},
            {"Service": "browserlab-playwright", "State": "running"},
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> object:
        del args
        captured_env.update(kwargs["env"])
        return type("Result", (), {"returncode": 0, "stdout": payload, "stderr": ""})()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=compose_dir,
        env={"HOME": str(home_dir), "PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
        repo_root_path=repo_root,
        repo_local_state=True,
    )

    assert captured_env["OPENCLAW_STATE_DIR"] == str((home_dir / ".openclaw").resolve())
    assert captured_env["STRONGCLAW_COMPOSE_STATE_DIR"] == str(
        resolve_repo_local_compose_state_dir(repo_root)
    )
    assert captured_env["OPENCLAW_CONFIG"] == str(
        (home_dir / ".openclaw" / "openclaw.json").resolve()
    )


def test_verify_compose_services_running_honors_repo_local_state_override_for_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variant compose probes should target the same repo-local project as ops up/down."""
    repo_root = tmp_path / "repo"
    compose_dir = repo_root / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    compose_file = compose_dir / "docker-compose.browser-lab.ci-hosted-macos.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    override_state_dir = home_dir / ".openclaw" / "repo-local-compose"
    captured_env: dict[str, str] = {}
    payload = json.dumps(
        [
            {"Service": "browserlab-proxy", "State": "running"},
            {"Service": "browserlab-playwright", "State": "running"},
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> object:
        del args
        captured_env.update(kwargs["env"])
        return type("Result", (), {"returncode": 0, "stdout": payload, "stderr": ""})()

    monkeypatch.setattr(fresh_host_shell.subprocess, "run", fake_run)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=compose_dir,
        env={
            "HOME": str(home_dir),
            "PATH": "/usr/bin",
            "STRONGCLAW_COMPOSE_VARIANT": "ci-hosted-macos",
            "STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR": str(override_state_dir),
        },
        expected_services=("browserlab-proxy", "browserlab-playwright"),
        repo_root_path=repo_root,
        repo_local_state=True,
    )

    resolved_override = override_state_dir.resolve()
    assert captured_env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"] == str(resolved_override)
    assert captured_env["STRONGCLAW_COMPOSE_STATE_DIR"] == str(resolved_override)
    assert captured_env["COMPOSE_PROJECT_NAME"] == compose_project_name(
        compose_name=compose_file.name,
        state_dir=resolved_override,
        repo_local_state=True,
        environ=captured_env,
    )


def test_exercise_linux_sidecars_waits_for_docker_backend_and_verifies_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux sidecar exercises should wait for Docker and verify runtime before teardown."""
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
    calls: list[str] = []

    monkeypatch.setattr(
        fresh_host_linux,
        "wait_for_docker_backend",
        lambda *, cwd, env: calls.append(f"wait:{cwd}"),
    )
    monkeypatch.setattr(
        fresh_host_linux,
        "run_command",
        lambda command, **kwargs: calls.append("run:" + " ".join(command)),
    )
    monkeypatch.setattr(
        fresh_host_linux,
        "verify_sidecar_services_running",
        lambda compose_file, **kwargs: calls.append(f"verify:{Path(compose_file).name}"),
    )

    fresh_host_linux.exercise_linux_sidecars(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("sidecars up --repo-local-state")
    assert calls[2] == "verify:docker-compose.aux-stack.yaml"
    assert calls[3].endswith("sidecars down --repo-local-state")


def test_exercise_linux_browser_lab_verifies_runtime_before_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux browser-lab exercises should prove runtime state before teardown."""
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
    calls: list[str] = []

    monkeypatch.setattr(
        fresh_host_linux,
        "wait_for_docker_backend",
        lambda *, cwd, env: calls.append(f"wait:{cwd}"),
    )
    monkeypatch.setattr(
        fresh_host_linux,
        "run_command",
        lambda command, **kwargs: calls.append("run:" + " ".join(command)),
    )
    monkeypatch.setattr(
        fresh_host_linux,
        "verify_compose_services_running",
        lambda compose_file, **kwargs: calls.append(f"verify:{Path(compose_file).name}"),
    )

    fresh_host_linux.exercise_linux_browser_lab(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("browser-lab up --repo-local-state")
    assert calls[2] == "verify:docker-compose.browser-lab.yaml"
    assert calls[3].endswith("browser-lab down --repo-local-state")


def test_macos_repo_local_sidecars_verifies_runtime_before_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted macOS sidecar exercises should verify runtime before teardown."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    calls: list[str] = []

    monkeypatch.setattr(
        fresh_host_macos,
        "wait_for_docker_backend",
        lambda *, cwd, env: calls.append(f"wait:{cwd}"),
    )
    monkeypatch.setattr(
        fresh_host_macos,
        "run_command",
        lambda command, **kwargs: calls.append("run:" + " ".join(command)),
    )
    monkeypatch.setattr(
        fresh_host_macos,
        "verify_sidecar_services_running",
        lambda compose_file, **kwargs: calls.append(f"verify:{Path(compose_file).name}"),
    )

    fresh_host_macos.exercise_macos_sidecars(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("sidecars up --repo-local-state")
    assert calls[2] == "verify:docker-compose.aux-stack.ci-hosted-macos.yaml"
    assert calls[3].endswith("sidecars down --repo-local-state")


def test_write_summary_includes_child_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summary rendering should include child runtime and image reports when present."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("DEFAULT_MACOS_RUNTIME_PROVIDER", "colima")
    monkeypatch.setenv("FRESH_HOST_PACKAGE_CACHE_ENABLED", "true")
    monkeypatch.setenv("FRESH_HOST_PACKAGE_CACHE_HIT", "false")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    report_path = Path(context.report_path)
    report = fresh_host.load_report(report_path)
    report.status = "success"
    report.phases.append(
        fresh_host.PhaseResult(
            name="bootstrap",
            status="success",
            duration_seconds=12.3,
            started_at="2026-03-25T00:00:00+00:00",
            finished_at="2026-03-25T00:00:12+00:00",
            command=["echo", "bootstrap"],
        )
    )
    fresh_host.write_report(report, report_path)
    Path(context.runtime_report_path or "").write_text(
        json.dumps(
            {
                "runtime_provider": "colima",
                "host_cpu_count": 4,
                "host_memory_gib": 8,
                "docker_host": "unix:///tmp/docker.sock",
                "failure_reason": None,
            }
        ),
        encoding="utf-8",
    )
    Path(context.image_report_path or "").write_text(
        json.dumps(
            {
                "images": ["postgres:16"],
                "missing_before_pull": ["postgres:16"],
                "pull_attempt_count": 2,
                "retried_images": ["postgres:16"],
                "pulled_images": ["postgres:16"],
                "failure_reason": None,
            }
        ),
        encoding="utf-8",
    )

    summary_path = tmp_path / "summary.md"
    fresh_host.write_summary(Path(context.context_path), summary_path)

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "## macOS Fresh Host Sidecars" in summary_text
    assert "| Package cache | true |" in summary_text
    assert "| Runtime provider | colima |" in summary_text
    assert "| Images requested | 1 |" in summary_text
