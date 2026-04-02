"""Contract checks for the fresh-host workflow surface."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, cast

import yaml

from tests.utils.helpers.repo import REPO_ROOT

_PYTHON_SCRIPT_INVOCATION_PATTERN = re.compile(
    r"(?P<prefix>(?:^|[\s;])(?:(?:uv\s+run\s+)?python3?\s+)?)"
    r"(?P<script>\./tests/scripts/[A-Za-z0-9_./-]+\.py)\b"
)
_NON_IMPACTFUL_PATH_FILTER_MARKERS = (
    '"**/*.md"',
    '"**/*.txt"',
    '"**/*.rst"',
    '"**/*.png"',
    '"**/*.jpg"',
    '"**/*.jpeg"',
    '"**/*.gif"',
    '"**/*.svg"',
    '"**/*.webp"',
    '"**/*.ico"',
    '"**/*.pdf"',
    '"LICENSE*"',
)
_CACHE_ACTION_NODE24_SHA = "668228422ae6a00e4ad889ee87cd7109ec5666a7"


def _workflow_text(workflow_name: str) -> str:
    """Return the requested workflow text."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / workflow_name
    return workflow_path.read_text(encoding="utf-8")


def _ci_gate_filters_text() -> str:
    """Return the CI gate path-filter definition text."""
    return (REPO_ROOT / ".github" / "ci" / "ci-gate-filters.yml").read_text(encoding="utf-8")


def _as_str_object_dict(value: object) -> dict[str, object] | None:
    """Return a string-keyed dictionary when the runtime value matches."""
    if not isinstance(value, dict):
        return None

    validated: dict[str, object] = {}
    raw_value = cast(dict[object, object], value)
    for key, entry in raw_value.items():
        if not isinstance(key, str):
            return None
        validated[key] = entry
    return validated


def _iter_workflow_python_script_invocations() -> Iterator[tuple[str, str, Path, bool]]:
    """Yield workflow shell invocations for repo-local Python helper scripts."""
    workflows_root = REPO_ROOT / ".github" / "workflows"

    for workflow_path in sorted(workflows_root.glob("*.yml")):
        loaded_workflow: object = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        workflow = _as_str_object_dict(loaded_workflow)
        if workflow is None:
            continue

        jobs = _as_str_object_dict(workflow.get("jobs"))
        if jobs is None:
            continue

        for job_value in jobs.values():
            job = _as_str_object_dict(job_value)
            if job is None:
                continue

            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            typed_steps = cast(list[object], steps)

            for step_value in typed_steps:
                step = _as_str_object_dict(step_value)
                if step is None:
                    continue
                run = step.get("run")
                step_name = str(step.get("name", "<unnamed>"))
                if not isinstance(run, str):
                    continue

                for line in run.splitlines():
                    stripped_line = line.strip()
                    if not stripped_line or stripped_line.startswith("#"):
                        continue

                    for match in _PYTHON_SCRIPT_INVOCATION_PATTERN.finditer(stripped_line):
                        script_token = match.group("script")
                        script_path = REPO_ROOT / script_token.removeprefix("./")
                        prefix = match.group("prefix") or ""
                        uses_python = "python" in prefix
                        yield workflow_path.name, step_name, script_path, uses_python


def test_fresh_host_acceptance_workflow_routes_to_reusable_core() -> None:
    """The trigger workflow should delegate execution to the reusable core workflow."""
    text = _workflow_text("fresh-host-acceptance.yml")

    assert "workflow_call:" in text
    assert "pull_request:" not in text
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "uses: ./.github/workflows/fresh-host-core.yml" in text


def test_ci_gate_workflow_runs_on_pull_requests_and_emits_verdict() -> None:
    """The CI gate should always run on pull requests and expose a stable verdict job."""
    text = _workflow_text("ci-gate.yml")

    assert "on:\n  pull_request:" in text
    assert "name: Verdict" in text
    assert "docs_parity_required" in text


def test_ci_gate_workflow_calls_reusable_heavy_lanes() -> None:
    """The CI gate should orchestrate heavy lanes through reusable workflow calls."""
    text = _workflow_text("ci-gate.yml")

    assert "uses: ./.github/workflows/harness.yml" in text
    assert "uses: ./.github/workflows/compatibility-matrix.yml" in text
    assert "uses: ./.github/workflows/memory-plugin-verification.yml" in text
    assert "uses: ./.github/workflows/fresh-host-acceptance.yml" in text
    assert "uses: ./.github/workflows/security.yml" in text


def test_heavy_pr_workflows_are_reusable_only() -> None:
    """PR-heavy workflows should be callable by the gate and not self-trigger on PRs."""
    for workflow_name in (
        "compatibility-matrix.yml",
        "harness.yml",
        "memory-plugin-verification.yml",
        "security.yml",
        "fresh-host-acceptance.yml",
    ):
        text = _workflow_text(workflow_name)
        assert "workflow_call:" in text, workflow_name
        assert "pull_request:" not in text, workflow_name


def test_fresh_host_core_workflow_uses_semantic_test_scripts() -> None:
    """Fresh-host core should delegate orchestration to dedicated scripts."""
    text = _workflow_text("fresh-host-core.yml")

    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/fresh_host.py preview-context" in text
    assert "./tests/scripts/fresh_host.py run-scenario" in text
    assert "./tests/scripts/fresh_host.py collect-diagnostics" in text
    assert "./tests/scripts/fresh_host.py cleanup" in text
    assert "./tests/scripts/fresh_host.py write-summary" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "./tests/scripts/hosted_docker.py restore-image-cache" in text
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
    """Fresh-host acceptance should keep explicit tuning inputs and concurrency guards."""
    text = _workflow_text("fresh-host-acceptance.yml")

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
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
    assert "inputs.enable_homebrew_cache" in text
    assert "inputs.enable_runtime_download_cache" in text
    assert "macos_runtime_provider: ${{ inputs.macos_runtime_provider }}" in text
    assert "docker_pull_parallelism: ${{ inputs.docker_pull_parallelism }}" in text
    assert "docker_pull_max_attempts: ${{ inputs.docker_pull_max_attempts }}" in text
    assert "enable_runtime_download_cache: ${{ inputs.enable_runtime_download_cache }}" in text
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
    assert "Restore hosted macOS Docker image cache" in text
    assert "Re-check hosted macOS Docker image cache after runtime install" in text
    assert f"actions/cache/restore@{_CACHE_ACTION_NODE24_SHA}" in text
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "package-manager-cache: false" in text
    assert "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR" in text
    assert "FRESH_HOST_DOCKER_IMAGE_CACHE_DIR" in text


def test_fresh_host_cache_warm_workflow_uses_semantic_cache_warmer() -> None:
    """Nightly cache warming should stay declarative and use the dedicated cache CLI."""
    text = _workflow_text("fresh-host-cache-warm.yml")

    assert "./tests/scripts/fresh_host_cache.py warm-packages" in text
    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/fresh_host.py preview-context" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "./tests/scripts/hosted_docker.py restore-image-cache" in text
    assert "./tests/scripts/hosted_docker.py ensure-images" in text
    assert "./tests/scripts/hosted_docker.py save-image-cache" in text
    assert f"actions/cache/restore@{_CACHE_ACTION_NODE24_SHA}" in text
    assert f"actions/cache/save@{_CACHE_ACTION_NODE24_SHA}" in text
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "actions/cache/save@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "Warm Linux Fresh Host Package Cache" in text
    assert "Warm macOS Fresh Host Caches" in text


def test_repo_workflows_do_not_embed_shell_blobs_or_python_heredocs() -> None:
    """Workflow run steps should stay thin across the repository."""
    workflows_root = REPO_ROOT / ".github" / "workflows"

    for workflow_path in workflows_root.glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "python - <<'PY'" not in text, workflow_path.as_posix()
        assert "python3 - <<'PY'" not in text, workflow_path.as_posix()
        assert "run: |" not in text, workflow_path.as_posix()


def test_workflow_python_script_invocations_are_executable_safe() -> None:
    """Workflow shell steps must not directly invoke non-executable Python helpers."""
    for (
        workflow_name,
        step_name,
        script_path,
        uses_python,
    ) in _iter_workflow_python_script_invocations():
        assert uses_python or os.access(script_path, os.X_OK), (
            f"{workflow_name}:{step_name} directly invokes {script_path} without a Python interpreter, "
            "but the script is not executable"
        )


def test_nightly_workflow_warms_caches_before_running_fresh_host_core() -> None:
    """Nightly should warm fresh-host caches before the long end-to-end acceptance run."""
    text = _workflow_text("nightly.yml")

    assert "uses: ./.github/workflows/fresh-host-cache-warm.yml" in text
    assert "uses: ./.github/workflows/fresh-host-core.yml" in text
    assert "needs: warm-fresh-host-caches" in text


def test_remaining_workflow_logic_routes_through_semantic_scripts() -> None:
    """Refactored workflow lanes should route operational logic through semantic scripts."""
    compatibility = _workflow_text("compatibility-matrix.yml")
    memory_plugin = _workflow_text("memory-plugin-verification.yml")
    nightly = _workflow_text("nightly.yml")
    security = _workflow_text("security.yml")
    release = _workflow_text("release.yml")

    assert "./tests/scripts/compatibility_matrix.py prepare-setup-smoke" in compatibility
    assert "./tests/scripts/compatibility_matrix.py assert-lossless-claw" in compatibility
    assert "./tests/scripts/compatibility_matrix.py assert-hypermemory-config" in compatibility
    assert "./tests/scripts/compatibility_matrix.py assert-openclaw-profiles" in nightly
    assert (
        "./tests/scripts/memory_plugin_verification.py run-clawops-memory-migration"
        in memory_plugin
    )
    assert "./tests/scripts/memory_plugin_verification.py run-vendored-host-checks" in memory_plugin
    assert "./tests/scripts/memory_plugin_verification.py wait-for-qdrant" in memory_plugin
    assert "./tests/scripts/security_workflow.py write-coverage-summary" in security
    assert "./tests/scripts/security_workflow.py install-gitleaks" in security
    assert "./tests/scripts/security_workflow.py install-syft" in security
    assert "./tests/scripts/security_workflow.py write-empty-sarif" in security
    assert "./tests/scripts/release_workflow.py clean-artifacts" in release
    assert "./tests/scripts/release_workflow.py verify-artifacts" in release
    assert "./tests/scripts/release_workflow.py publish-github-release" in release


def test_release_workflow_blocks_publish_on_fresh_host_and_memory_plugin_prerequisites() -> None:
    """Release publication should depend on reusable fresh-host and plugin verification jobs."""
    workflow = yaml.safe_load(_workflow_text("release.yml"))
    jobs = workflow["jobs"]

    assert (
        jobs["release-fresh-host-acceptance"]["uses"] == "./.github/workflows/fresh-host-core.yml"
    )
    assert (
        jobs["release-memory-plugin-verification"]["uses"]
        == "./.github/workflows/memory-plugin-verification.yml"
    )
    assert "release-fresh-host-acceptance" in jobs["publish-release-artifacts"]["needs"]
    assert "release-memory-plugin-verification" in jobs["publish-release-artifacts"]["needs"]


def test_memory_plugin_workflow_supports_reusable_workflow_invocation() -> None:
    """The memory-plugin workflow should stay callable from the release workflow."""
    text = _workflow_text("memory-plugin-verification.yml")

    assert "workflow_call:" in text


def test_selected_workflows_ignore_docs_and_static_only_changes() -> None:
    """Reusable lanes and gate filters should skip docs-only and static-only changes."""
    for workflow_name in (
        "compatibility-matrix.yml",
        "dependency-submission.yml",
        "memory-plugin-verification.yml",
        "security.yml",
    ):
        text = _workflow_text(workflow_name)
        assert "paths-ignore:" in text, workflow_name
        for marker in _NON_IMPACTFUL_PATH_FILTER_MARKERS:
            assert marker in text, workflow_name

    filters_text = _ci_gate_filters_text()
    for marker in _NON_IMPACTFUL_PATH_FILTER_MARKERS:
        marker_body = marker.strip('"')
        negated_marker = f'"!{marker_body}"'
        assert marker in filters_text or negated_marker in filters_text


def test_devflow_contract_workflow_surfaces_public_devflow_lane() -> None:
    text = _workflow_text("devflow-contract.yml")

    assert "uv sync --locked" in text
    assert "uv run python -m compileall -q src tests" in text
    assert 'uv run clawops devflow plan --project-root . --goal "contract smoke"' in text
    assert '"platform/docs/DEVFLOW.md"' not in text


def test_security_harness_tracks_the_context_provider_namespace() -> None:
    text = (REPO_ROOT / "platform/configs/harness/security_regressions.yaml").read_text(
        encoding="utf-8"
    )

    assert "id: context-cli-smoke" in text
    assert 'python", "-m", "clawops", "context", "--help"' in text
    assert 'stdout_contains: ["codebase"]' in text


def test_codeql_config_ignores_packaged_runtime_asset_mirror() -> None:
    """CodeQL should scan the maintained source tree, not the packaged asset mirror."""
    text = (REPO_ROOT / "security/codeql/codeql-config.yml").read_text(encoding="utf-8")

    assert "src/clawops/assets" in text
