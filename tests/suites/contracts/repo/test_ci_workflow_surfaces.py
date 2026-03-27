"""Contract checks for the fresh-host workflow surface."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def _workflow_text(workflow_name: str) -> str:
    """Return the requested workflow text."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / workflow_name
    return workflow_path.read_text(encoding="utf-8")


def test_fresh_host_acceptance_workflow_routes_to_reusable_core() -> None:
    """The trigger workflow should delegate execution to the reusable core workflow."""
    text = _workflow_text("fresh-host-acceptance.yml")

    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "uses: ./.github/workflows/fresh-host-core.yml" in text


def test_fresh_host_core_workflow_uses_semantic_test_scripts() -> None:
    """Fresh-host core should delegate orchestration to dedicated scripts."""
    text = _workflow_text("fresh-host-core.yml")

    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/fresh_host.py run-scenario" in text
    assert "./tests/scripts/fresh_host.py collect-diagnostics" in text
    assert "./tests/scripts/fresh_host.py cleanup" in text
    assert "./tests/scripts/fresh_host.py write-summary" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "./tests/scripts/hosted_docker.py ensure-images" in text
    assert "./tests/scripts/hosted_docker.py collect-diagnostics" in text


def test_fresh_host_core_workflow_stays_thin() -> None:
    """Fresh-host core should avoid embedded programs and shell blobs."""
    text = _workflow_text("fresh-host-core.yml")

    assert "python - <<'PY'" not in text
    assert "python3 - <<'PY'" not in text
    assert "run: |" not in text
    assert ".github/scripts/fresh_host_images.py" not in text


def test_fresh_host_workflow_preserves_dispatch_inputs_and_concurrency_controls() -> None:
    """The trigger workflow should keep dispatch tuning and concurrency guards."""
    text = _workflow_text("fresh-host-acceptance.yml")

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
    assert "cancel-in-progress: true" in text


def test_fresh_host_core_workflow_preserves_current_macos_matrix_and_variant_support() -> None:
    """Fresh-host core should keep the current sidecars/browser-lab macOS split."""
    text = _workflow_text("fresh-host-core.yml")

    assert "macOS Fresh Host Sidecars" in text
    assert "macOS Fresh Host Browser Lab" in text
    assert "scenario_id: macos-sidecars" in text
    assert "scenario_id: macos-browser-lab" in text


def test_fresh_host_core_workflow_preserves_cache_restore_surface() -> None:
    """Fresh-host core should keep the current package, Homebrew, and runtime restores."""
    text = _workflow_text("fresh-host-core.yml")

    assert "FRESH_HOST_CACHE_ROOT" in text
    assert "UV_CACHE_DIR" in text
    assert "npm_config_cache" in text
    assert "npm_config_prefer_offline" in text
    assert "HOMEBREW_CACHE" in text
    assert "Restore package download caches" in text
    assert "Restore hosted macOS Homebrew download cache" in text
    assert "Restore hosted macOS runtime download cache" in text
    assert "actions/cache/restore" in text
    assert "package-manager-cache: false" in text
    assert "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR" in text


def test_fresh_host_cache_warm_workflow_uses_semantic_cache_warmer() -> None:
    """Nightly cache warming should stay declarative and use the dedicated cache CLI."""
    text = _workflow_text("fresh-host-cache-warm.yml")

    assert "./tests/scripts/fresh_host_cache.py warm-packages" in text
    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "actions/cache/restore" in text
    assert "actions/cache/save" in text
    assert "Warm Linux Fresh Host Package Cache" in text
    assert "Warm macOS Fresh Host Caches" in text


def test_nightly_workflow_warms_caches_before_running_fresh_host_core() -> None:
    """Nightly should warm fresh-host caches before the long end-to-end acceptance run."""
    text = _workflow_text("nightly.yml")

    assert "uses: ./.github/workflows/fresh-host-cache-warm.yml" in text
    assert "uses: ./.github/workflows/fresh-host-core.yml" in text
    assert "needs: warm-fresh-host-caches" in text
