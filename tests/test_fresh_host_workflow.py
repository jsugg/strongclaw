"""Regression checks for the fresh-host acceptance workflow."""

from __future__ import annotations

import pathlib

import yaml


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _workflow_path() -> pathlib.Path:
    return _repo_root() / ".github" / "workflows" / "fresh-host-acceptance.yml"


def test_workflow_dispatch_supports_cache_benchmarks() -> None:
    workflow = yaml.safe_load(_workflow_path().read_text(encoding="utf-8"))

    dispatch = workflow.get("on", workflow[True])["workflow_dispatch"]["inputs"]

    assert dispatch["enable_package_cache"]["type"] == "boolean"
    assert dispatch["enable_homebrew_cache"]["type"] == "boolean"
    assert dispatch["enable_docker_image_cache"]["type"] == "boolean"


def test_macos_job_runs_the_full_flow_for_all_events() -> None:
    workflow_text = _workflow_path().read_text(encoding="utf-8")
    macos_section = workflow_text.split("  macos-fresh-host:\n", maxsplit=1)[1]

    assert "github.event_name != 'pull_request'" not in workflow_text
    assert 'if [[ "${GITHUB_EVENT_NAME}" == "pull_request" ]]' not in workflow_text
    assert "--no-activate-services" not in macos_section
    assert "runs-on: macos-15-intel" in macos_section
    assert "macos_runner_label" not in workflow_text
    assert "macos_runtime_provider" not in workflow_text
    assert "Warm hosted macOS images" in macos_section
    assert ".github/scripts/fresh_host_images.py pull" in macos_section
    assert "FRESH_HOST_DOCKER_PULL_PARALLELISM" in macos_section
    assert "Exercise macOS repo-local sidecars" in macos_section
    assert "Exercise macOS repo-local browser-lab" in macos_section
    assert "STRONGCLAW_COMPOSE_VARIANT: ci-hosted-macos" in macos_section
    assert "docker-compose.aux-stack.ci-hosted-macos.yaml" in macos_section
    assert "docker-compose.browser-lab.ci-hosted-macos.yaml" in macos_section


def test_fresh_host_workflow_writes_summaries_and_uploads_reports() -> None:
    workflow_text = _workflow_path().read_text(encoding="utf-8")

    assert "Write Linux summary" in workflow_text
    assert "Write macOS summary" in workflow_text
    assert "GITHUB_STEP_SUMMARY" in workflow_text
    assert "fresh-host-reports/linux" in workflow_text
    assert "fresh-host-reports/macos" in workflow_text
    assert "Restore hosted macOS Docker image cache" in workflow_text
    assert "Load hosted macOS Docker image cache" in workflow_text
    assert "Snapshot hosted macOS Docker image cache" in workflow_text
    assert ".github/scripts/fresh_host_images.py save" in workflow_text
