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

    assert dispatch["macos_runtime_provider"]["default"] == "colima"
    assert dispatch["docker_pull_parallelism"]["default"] == "3"
    assert dispatch["enable_package_cache"]["default"] is True
    assert dispatch["enable_homebrew_cache"]["default"] is True
    assert dispatch["enable_docker_image_cache"]["default"] is True
    assert dispatch["enable_runtime_download_cache"]["default"] is True
    assert dispatch["enable_package_cache"]["type"] == "boolean"
    assert dispatch["enable_homebrew_cache"]["type"] == "boolean"
    assert dispatch["enable_docker_image_cache"]["type"] == "boolean"
    assert dispatch["enable_runtime_download_cache"]["type"] == "boolean"


def test_workflow_dispatch_concurrency_keeps_distinct_benchmarks_alive() -> None:
    workflow_text = _workflow_path().read_text(encoding="utf-8")

    assert "github.event_name == 'workflow_dispatch'" in workflow_text
    assert "inputs.macos_runtime_provider" in workflow_text
    assert "inputs.docker_pull_parallelism" in workflow_text
    assert "inputs.enable_runtime_download_cache" in workflow_text


def test_macos_job_runs_the_full_flow_for_all_events() -> None:
    workflow_text = _workflow_path().read_text(encoding="utf-8")
    macos_section = workflow_text.split("  macos-fresh-host:\n", maxsplit=1)[1]

    assert "github.event_name != 'pull_request'" not in workflow_text
    assert 'if [[ "${GITHUB_EVENT_NAME}" == "pull_request" ]]' not in workflow_text
    assert "--no-activate-services" not in macos_section
    assert "runs-on: macos-15-intel" in macos_section
    assert "macos_runner_label" not in workflow_text
    assert "Ensure hosted macOS images" in macos_section
    assert ".github/scripts/fresh_host_images.py ensure" in macos_section
    assert "FRESH_HOST_SELECTED_DOCKER_PULL_PARALLELISM" in macos_section
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
    assert "fresh-host-docker-images-v3-" in workflow_text
    assert "Ensure hosted macOS images" in workflow_text
    assert ".github/scripts/fresh_host_images.py ensure" in workflow_text
    assert "image-ensure-report.json" in workflow_text
    assert "Package cache hit" in workflow_text
    assert "Homebrew cache hit" in workflow_text
    assert "Docker image cache hit" in workflow_text
    assert "Runtime download cache" in workflow_text
    assert "Restore hosted macOS runtime download cache" in workflow_text


def test_fresh_host_workflow_points_tools_at_restored_cache_dirs() -> None:
    workflow_text = _workflow_path().read_text(encoding="utf-8")

    assert "FRESH_HOST_CACHE_ROOT" in workflow_text
    assert "UV_CACHE_DIR" in workflow_text
    assert "npm_config_cache" in workflow_text
    assert "npm_config_prefer_offline" in workflow_text
    assert "package-manager-cache: false" in workflow_text
    assert "HOMEBREW_CACHE" in workflow_text
    assert "path: ${{ env.HOMEBREW_CACHE }}" in workflow_text
    assert 'cache_args+=(--cache-dir "${FRESH_HOST_DOCKER_IMAGE_CACHE_DIR}")' in workflow_text
    assert "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR" in workflow_text
    assert '"Colima binary"' in workflow_text
    assert '"Lima payload"' in workflow_text
    assert (
        "Runtime provider=${runtime_provider} cache-enabled=${runtime_download_cache_enabled}"
        in workflow_text
    )
    assert "MACOS_ORBSTACK_VERSION" in workflow_text
    assert "Hosted CI must use colima" in workflow_text
