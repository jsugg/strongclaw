"""Unit coverage for fresh-host CI helpers."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from clawops.strongclaw_compose import compose_project_name
from clawops.strongclaw_runtime import resolve_repo_local_compose_state_dir
from tests.plugins.infrastructure.context import TestContext
from tests.scripts import fresh_host as fresh_host_script
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import linux as fresh_host_linux
from tests.utils.helpers._fresh_host import macos as fresh_host_macos
from tests.utils.helpers._fresh_host import reporting as fresh_host_reporting
from tests.utils.helpers._fresh_host import scenario as fresh_host_scenario
from tests.utils.helpers._fresh_host import shell as fresh_host_shell

_parse_args = cast(
    Callable[[list[str] | None], argparse.Namespace],
    cast(Any, fresh_host_script)._parse_args,
)
_run_named_phase = cast(
    Callable[[fresh_host.FreshHostContext, str], list[str] | None],
    cast(Any, fresh_host)._run_named_phase,
)
_venv_clawops_command = cast(
    Callable[..., list[str]],
    cast(Any, fresh_host)._venv_clawops_command,
)


def test_prepare_context_writes_context_and_env_file(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Context preparation should persist files and downstream env exports."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_push")

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
    test_context: TestContext,
) -> None:
    """Browser-lab scenarios should skip machine-name and launchd phases."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

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
    test_context: TestContext,
) -> None:
    """Sidecars scenarios should tear down host services before repo-local exercise."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

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
    test_context: TestContext,
) -> None:
    """Sidecars scenarios should export the hosted macOS compose variant details."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

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
    sidecars = _parse_args(
        [
            "prepare-context",
            "--scenario",
            "macos-sidecars",
            "--runner-temp",
            "/tmp/runner",
        ]
    )
    browser_lab = _parse_args(
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
    test_context: TestContext,
) -> None:
    """The sidecars macOS scenario should dispatch the deactivation phase."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    def _fake_deactivate_services(loaded_context: fresh_host.FreshHostContext) -> list[str]:
        return ["echo", loaded_context.scenario_id, "deactivate-services"]

    test_context.patch.patch_object(
        fresh_host_macos,
        "deactivate_macos_host_services",
        new=_fake_deactivate_services,
    )

    assert _run_named_phase(context, "deactivate-services") == [
        "echo",
        "macos-sidecars",
        "deactivate-services",
    ]


def test_run_scenario_records_successful_phase_sequence(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Scenario execution should append one successful phase result per planned phase."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_push")
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

    test_context.patch.patch_object(
        fresh_host_scenario,
        "run_named_phase",
        new=fake_run_named_phase,
    )

    report = fresh_host.run_scenario(Path(context.context_path))

    assert report.status == "success"
    assert calls == fresh_host.scenario_phase_names(context)
    assert [phase.name for phase in report.phases] == calls
    assert all(phase.status == "success" for phase in report.phases)


def test_venv_clawops_command_preserves_virtualenv_entrypoint_path(
    tmp_path: Path,
    test_context: TestContext,
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
    test_context.apply_profiles("fresh_host_push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    command = _venv_clawops_command(context, "setup")

    assert command[0] == str(venv_bin / "python")
    assert command[0] != str(target_python.resolve())


def test_wait_for_docker_backend_retries_after_transient_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Transient Linux docker probe failures should be retried before giving up."""
    attempts = {"count": 0}

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del args, cwd, env, check, timeout, text, capture_output
        attempts["count"] += 1
        if attempts["count"] < 3:
            return subprocess.CompletedProcess(args=["docker"], returncode=1)
        return subprocess.CompletedProcess(args=["docker"], returncode=0)

    def fake_sleep(_: float) -> None:
        return None

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_shell.time, "sleep", new=fake_sleep)

    fresh_host_shell.wait_for_docker_backend(cwd=tmp_path, env={"PATH": "/usr/bin"})

    assert attempts["count"] == 3


def test_verify_compose_services_running_accepts_json_lines_output(
    tmp_path: Path,
    test_context: TestContext,
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

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del args, cwd, env, check, timeout, text, capture_output
        return subprocess.CompletedProcess(
            args=["docker", "compose"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )


def test_verify_compose_services_running_requires_healthy_services(
    tmp_path: Path,
    test_context: TestContext,
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

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)

    with pytest.raises(fresh_host.FreshHostError, match="health 'unhealthy'"):
        fresh_host_shell.verify_compose_services_running(
            compose_file,
            cwd=tmp_path,
            env={"PATH": "/usr/bin"},
            expected_services=("postgres",),
            healthy_services=("postgres",),
            timeout_seconds=0,
        )


def test_verify_compose_services_running_retries_until_service_health_recovers(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Compose runtime verification should tolerate transient unhealthy health checks."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payloads = iter(
        (
            json.dumps(
                [
                    {"Service": "postgres", "State": "running", "Health": "unhealthy"},
                ]
            ),
            json.dumps(
                [
                    {"Service": "postgres", "State": "running", "Health": "healthy"},
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

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_shell.time, "sleep", new=sleeps.append)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("postgres",),
        healthy_services=("postgres",),
        timeout_seconds=10,
    )

    assert sleeps == [2.0]


def test_verify_compose_services_running_ignores_unexpected_completed_services(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Compose runtime verification should ignore unrelated exited services."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payload = json.dumps(
        [
            {"Service": "postgres", "State": "running", "Health": "healthy"},
            {"Service": "bootstrap-helper", "State": "exited", "ExitCode": 0},
            {"Service": "litellm", "State": "running", "Health": "healthy"},
            {"Service": "otel-collector", "State": "running"},
            {"Service": "qdrant", "State": "running", "Health": "healthy"},
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return type("Result", (), {"returncode": 0, "stdout": payload, "stderr": ""})()

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)

    fresh_host_shell.verify_sidecar_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
    )


def test_verify_compose_services_running_retries_when_compose_ps_is_temporarily_empty(
    tmp_path: Path,
    test_context: TestContext,
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

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_shell.time, "sleep", new=sleeps.append)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )

    assert sleeps == [2.0]


def test_verify_compose_services_running_retries_until_services_are_ready(
    tmp_path: Path,
    test_context: TestContext,
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

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_shell.time, "sleep", new=sleeps.append)

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        expected_services=("browserlab-proxy", "browserlab-playwright"),
    )

    assert sleeps == [2.0]


def test_verify_compose_services_running_uses_scenario_home_for_compose_env(
    tmp_path: Path,
    test_context: TestContext,
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

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del args, cwd, check, timeout, text, capture_output
        assert env is not None
        captured_env.update(env)
        return subprocess.CompletedProcess(
            args=["docker", "compose"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)

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


def test_verify_compose_services_running_ignores_isolated_runtime_keys_from_local_env(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Compose probes should not accept legacy runtime-path keys when isolation is active."""
    repo_root = tmp_path / "repo"
    compose_dir = repo_root / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    compose_file = compose_dir / "docker-compose.browser-lab.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    runtime_root = home_dir / "runtime-root"
    local_env_file = tmp_path / "legacy.env.local"
    local_env_file.write_text(
        "\n".join(
            (
                "OPENCLAW_STATE_DIR=/tmp/legacy-openclaw-state",
                "OPENCLAW_CONFIG_PATH=/tmp/legacy-openclaw.json",
                "OPENCLAW_PROFILE=legacy-profile",
                "NEO4J_PASSWORD=repo-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    captured_env: dict[str, str] = {}
    payload = json.dumps(
        [
            {"Service": "browserlab-proxy", "State": "running"},
            {"Service": "browserlab-playwright", "State": "running"},
        ]
    )

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del args, cwd, check, timeout, text, capture_output
        assert env is not None
        captured_env.update(env)
        return subprocess.CompletedProcess(
            args=["docker", "compose"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

    def _varlock_local_env_file(
        _repo_root: Path,
        *,
        home_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Path:
        del home_dir, environ
        return local_env_file

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(
        fresh_host_shell,
        "varlock_local_env_file",
        new=_varlock_local_env_file,
    )

    fresh_host_shell.verify_compose_services_running(
        compose_file,
        cwd=compose_dir,
        env={
            "HOME": str(home_dir),
            "PATH": "/usr/bin",
            "STRONGCLAW_RUNTIME_ROOT": str(runtime_root),
        },
        expected_services=("browserlab-proxy", "browserlab-playwright"),
        repo_root_path=repo_root,
        repo_local_state=True,
    )

    expected_state_dir = (runtime_root / ".openclaw").resolve()
    expected_config_path = expected_state_dir / "openclaw.json"
    assert captured_env["OPENCLAW_HOME"] == str(runtime_root.resolve())
    assert captured_env["OPENCLAW_STATE_DIR"] == str(expected_state_dir)
    assert captured_env["OPENCLAW_CONFIG_PATH"] == str(expected_config_path)
    assert captured_env["OPENCLAW_CONFIG"] == str(expected_config_path)
    assert captured_env["OPENCLAW_PROFILE"] == "strongclaw-dev"
    assert captured_env["NEO4J_PASSWORD"] == "repo-secret"


def test_verify_compose_services_running_honors_repo_local_state_override_for_variants(
    tmp_path: Path,
    test_context: TestContext,
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

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del args, cwd, check, timeout, text, capture_output
        assert env is not None
        captured_env.update(env)
        return subprocess.CompletedProcess(
            args=["docker", "compose"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

    test_context.patch.patch_object(fresh_host_shell.subprocess, "run", new=fake_run)

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
    test_context: TestContext,
) -> None:
    """Linux sidecar exercises should wait for Docker and verify runtime before teardown."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    calls: list[str] = []

    def _fake_wait_for_docker_backend(*, cwd: Path, env: dict[str, str]) -> None:
        del env
        calls.append(f"wait:{cwd}")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        check: bool = True,
    ) -> None:
        del cwd, env, timeout_seconds, check
        calls.append("run:" + " ".join(command))

    def _fake_verify_sidecars(
        compose_file: Path,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 120,
        repo_root_path: Path | None = None,
        repo_local_state: bool = False,
    ) -> None:
        del cwd, env, timeout_seconds, repo_root_path, repo_local_state
        calls.append(f"verify:{compose_file.name}")

    test_context.patch.patch_object(
        fresh_host_linux,
        "wait_for_docker_backend",
        new=_fake_wait_for_docker_backend,
    )
    test_context.patch.patch_object(
        fresh_host_linux,
        "run_command",
        new=_fake_run_command,
    )
    test_context.patch.patch_object(
        fresh_host_linux,
        "verify_sidecar_services_running",
        new=_fake_verify_sidecars,
    )

    fresh_host_linux.exercise_linux_sidecars(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("sidecars up --repo-local-state")
    assert calls[2] == "verify:docker-compose.aux-stack.yaml"
    assert calls[3].endswith("sidecars down --repo-local-state")


def test_exercise_linux_browser_lab_verifies_runtime_before_teardown(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Linux browser-lab exercises should prove runtime state before teardown."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_push")

    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    calls: list[str] = []

    def _fake_wait_for_docker_backend(*, cwd: Path, env: dict[str, str]) -> None:
        del env
        calls.append(f"wait:{cwd}")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        check: bool = True,
    ) -> None:
        del cwd, env, timeout_seconds, check
        calls.append("run:" + " ".join(command))

    def _fake_verify_compose_services(
        compose_file: Path,
        *,
        cwd: Path,
        env: dict[str, str],
        expected_services: tuple[str, ...],
        healthy_services: tuple[str, ...] = (),
        timeout_seconds: int = 120,
        repo_root_path: Path | None = None,
        repo_local_state: bool = False,
    ) -> None:
        del cwd, env, expected_services, healthy_services, timeout_seconds, repo_root_path
        del repo_local_state
        calls.append(f"verify:{compose_file.name}")

    test_context.patch.patch_object(
        fresh_host_linux,
        "wait_for_docker_backend",
        new=_fake_wait_for_docker_backend,
    )
    test_context.patch.patch_object(
        fresh_host_linux,
        "run_command",
        new=_fake_run_command,
    )
    test_context.patch.patch_object(
        fresh_host_linux,
        "verify_compose_services_running",
        new=_fake_verify_compose_services,
    )

    fresh_host_linux.exercise_linux_browser_lab(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("browser-lab up --repo-local-state")
    assert calls[2] == "verify:docker-compose.browser-lab.yaml"
    assert calls[3].endswith("browser-lab down --repo-local-state")


def test_macos_repo_local_sidecars_verifies_runtime_before_teardown(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Hosted macOS sidecar exercises should verify runtime before teardown."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    calls: list[str] = []
    verify_kwargs: dict[str, object] = {}

    def _fake_wait_for_docker_backend(*, cwd: Path, env: dict[str, str]) -> None:
        del env
        calls.append(f"wait:{cwd}")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        check: bool = True,
    ) -> None:
        del cwd, env, timeout_seconds, check
        calls.append("run:" + " ".join(command))

    test_context.patch.patch_object(
        fresh_host_macos,
        "wait_for_docker_backend",
        new=_fake_wait_for_docker_backend,
    )
    test_context.patch.patch_object(
        fresh_host_macos,
        "run_command",
        new=_fake_run_command,
    )

    def fake_verify_sidecars(compose_file: Path, **kwargs: object) -> None:
        verify_kwargs.update(kwargs)
        calls.append(f"verify:{Path(compose_file).name}")

    test_context.patch.patch_object(
        fresh_host_macos,
        "verify_sidecar_services_running",
        new=fake_verify_sidecars,
    )

    fresh_host_macos.exercise_macos_sidecars(context)

    assert calls[0].startswith("wait:")
    assert calls[1].endswith("sidecars up --repo-local-state")
    assert calls[2] == "verify:docker-compose.aux-stack.ci-hosted-macos.yaml"
    assert calls[3].endswith("sidecars down --repo-local-state")
    assert (
        verify_kwargs["timeout_seconds"]
        == fresh_host_macos.HOSTED_MACOS_SIDECAR_STARTUP_TIMEOUT_SECONDS
    )
    assert verify_kwargs["repo_local_state"] is True


def test_deactivate_macos_host_services_limits_teardown_to_active_sidecars_services(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """macOS sidecars teardown should skip browser-lab commands entirely."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    inspected_labels: list[str] = []
    executed_commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, check, capture_output, text, timeout
        assert command[:2] == ["launchctl", "print"]
        label = command[2].rsplit("/", maxsplit=1)[1]
        inspected_labels.append(label)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_best_effort(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> str | None:
        del cwd, env
        executed_commands.append(command)
        return None

    test_context.patch.patch_object(fresh_host_macos.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_macos, "best_effort", new=fake_best_effort)

    command = fresh_host_macos.deactivate_macos_host_services(context)

    assert command is not None
    assert inspected_labels == ["ai.openclaw.gateway", "ai.openclaw.sidecars"]
    assert len(executed_commands) == 3
    assert executed_commands[0][:2] == ["launchctl", "bootout"]
    assert executed_commands[0][-1].endswith("ai.openclaw.gateway.plist")
    assert executed_commands[1][:2] == ["launchctl", "bootout"]
    assert executed_commands[1][-1].endswith("ai.openclaw.sidecars.plist")
    assert executed_commands[2][-2:] == ["sidecars", "down"]
    assert all("browserlab" not in " ".join(step) for step in executed_commands)
    assert all("browser-lab" not in " ".join(step) for step in executed_commands)


def test_deactivate_macos_host_services_raises_for_active_bootout_failure(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """macOS teardown should fail when an active launchd bootout fails."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    executed_commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, check, capture_output, text, timeout
        assert command[:2] == ["launchctl", "print"]
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_best_effort(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> str | None:
        del cwd, env
        executed_commands.append(command)
        if command[:2] == ["launchctl", "bootout"] and command[-1].endswith(
            "ai.openclaw.sidecars.plist"
        ):
            return (
                "launchctl bootout gui/501 /tmp/ai.openclaw.sidecars.plist failed: "
                "Boot-out failed: 5: Input/output error"
            )
        return None

    test_context.patch.patch_object(fresh_host_macos.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_macos, "best_effort", new=fake_best_effort)

    with pytest.raises(fresh_host.FreshHostError, match="Boot-out failed: 5: Input/output error"):
        fresh_host_macos.deactivate_macos_host_services(context)

    assert any(
        command[:2] == ["launchctl", "bootout"]
        and command[-1].endswith("ai.openclaw.sidecars.plist")
        for command in executed_commands
    )


def test_cleanup_macos_skips_clawops_teardown_when_venv_is_missing(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Cleanup should remain best-effort when setup never created the managed venv."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    executed_commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, check, capture_output, text, timeout
        assert command[:2] == ["launchctl", "print"]
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_best_effort(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> str | None:
        del cwd, env
        executed_commands.append(command)
        return None

    test_context.patch.patch_object(fresh_host_macos.subprocess, "run", new=fake_run)
    test_context.patch.patch_object(fresh_host_macos, "best_effort", new=fake_best_effort)

    result = fresh_host_macos.cleanup_macos(context)

    assert len(executed_commands) == 2
    assert all(command[:2] == ["launchctl", "bootout"] for command in executed_commands)
    assert all(".venv/bin/python" not in " ".join(command) for command in executed_commands)
    assert any("managed venv entrypoint is missing" in note for note in result.notes)
    assert result.command == executed_commands[-1]


def test_cleanup_cli_returns_nonzero_and_records_failure_when_cleanup_raises(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Cleanup should persist failure state and return a nonzero CLI exit code."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )

    def fake_cleanup_macos(_: fresh_host.FreshHostContext) -> object:
        raise fresh_host.FreshHostError("cleanup exploded")

    test_context.patch.patch_object(
        fresh_host_reporting,
        "cleanup_macos",
        new=fake_cleanup_macos,
    )

    exit_code = fresh_host_script.main(["cleanup", "--context", str(context.context_path)])
    report = fresh_host.load_report(Path(context.report_path))

    assert exit_code == 1
    assert report.status == "failure"
    assert report.failure_reason == "cleanup exploded"
    assert report.phases[-1].name == "cleanup"
    assert report.phases[-1].status == "failure"
    assert report.phases[-1].failure_reason == "cleanup exploded"


def test_collect_diagnostics_uses_compose_probe_env_for_macos_compose_commands(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Fresh-host diagnostics should reuse compose probe env for compose commands."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")
    local_env_file = workspace / "platform" / "configs" / "varlock" / ".env.local"
    local_env_file.parent.mkdir(parents=True, exist_ok=True)
    local_env_file.write_text("NEO4J_PASSWORD=diag-secret\n", encoding="utf-8")

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    captured: list[tuple[list[str], dict[str, str]]] = []

    def fake_capture_to_file(
        command: list[str],
        *,
        output_path: Path,
        cwd: Path,
        env: dict[str, str],
    ) -> None:
        del output_path, cwd
        captured.append((command, env))
        return None

    test_context.patch.patch_object(
        fresh_host_reporting,
        "capture_to_file",
        new=fake_capture_to_file,
    )

    fresh_host.collect_diagnostics(Path(context.context_path))

    compose_envs = [env for command, env in captured if command[:3] == ["docker", "compose", "-f"]]
    assert compose_envs
    assert all(env["NEO4J_PASSWORD"] == "diag-secret" for env in compose_envs)
    assert all("COMPOSE_PROJECT_NAME" in env for env in compose_envs)


def test_write_summary_includes_child_reports(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Summary rendering should include child runtime and image reports when present."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_colima")
    test_context.env.update(
        {
            "FRESH_HOST_PACKAGE_CACHE_ENABLED": "true",
            "FRESH_HOST_PACKAGE_CACHE_HIT": "false",
        }
    )

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
