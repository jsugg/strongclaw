"""Contract checks for the fresh-host workflow surface."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def _workflow_text() -> str:
    """Return the fresh-host workflow text."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "fresh-host-acceptance.yml"
    return workflow_path.read_text(encoding="utf-8")


def test_fresh_host_workflow_uses_semantic_test_scripts() -> None:
    """Fresh-host workflow should delegate orchestration to dedicated scripts."""
    text = _workflow_text()

    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/fresh_host.py run-scenario" in text
    assert "./tests/scripts/fresh_host.py collect-diagnostics" in text
    assert "./tests/scripts/fresh_host.py cleanup" in text
    assert "./tests/scripts/fresh_host.py write-summary" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "./tests/scripts/hosted_docker.py ensure-images" in text
    assert "./tests/scripts/hosted_docker.py collect-diagnostics" in text


def test_fresh_host_workflow_stays_thin() -> None:
    """Fresh-host workflow should avoid embedded programs and shell blobs."""
    text = _workflow_text()

    assert "python - <<'PY'" not in text
    assert "python3 - <<'PY'" not in text
    assert "run: |" not in text
    assert ".github/scripts/fresh_host_images.py" not in text


def test_fresh_host_workflow_preserves_dispatch_inputs_and_concurrency_controls() -> None:
    """Workflow dispatch inputs should still support cache and pull tuning."""
    text = _workflow_text()

    assert "macos_runtime_provider" in text
    assert "docker_pull_parallelism" in text
    assert "docker_pull_max_attempts" in text
    assert "enable_package_cache" in text
    assert "enable_homebrew_cache" in text
    assert "enable_runtime_download_cache" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "inputs.macos_runtime_provider" in text
    assert "inputs.docker_pull_parallelism" in text
    assert "inputs.docker_pull_max_attempts" in text
    assert "inputs.enable_runtime_download_cache" in text


def test_fresh_host_workflow_preserves_current_macos_matrix_and_variant_support() -> None:
    """Workflow should keep the current sidecars/browser-lab macOS split."""
    text = _workflow_text()

    assert "macOS Fresh Host Sidecars" in text
    assert "macOS Fresh Host Browser Lab" in text
    assert "scenario_id: macos-sidecars" in text
    assert "scenario_id: macos-browser-lab" in text


def test_fresh_host_workflow_preserves_cache_restore_surface() -> None:
    """Workflow should keep the current package, Homebrew, and runtime caches."""
    text = _workflow_text()

    assert "FRESH_HOST_CACHE_ROOT" in text
    assert "UV_CACHE_DIR" in text
    assert "npm_config_cache" in text
    assert "npm_config_prefer_offline" in text
    assert "HOMEBREW_CACHE" in text
    assert "Restore package download caches" in text
    assert "Restore hosted macOS Homebrew download cache" in text
    assert "Restore hosted macOS runtime download cache" in text
    assert "package-manager-cache: false" in text
    assert "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR" in text
