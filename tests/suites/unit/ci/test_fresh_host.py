"""Unit coverage for fresh-host CI helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.scripts import fresh_host as fresh_host_script
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import macos as fresh_host_macos
from tests.utils.helpers._fresh_host import scenario as fresh_host_scenario


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
