"""Contract checks for the fresh-host workflow surface."""

from __future__ import annotations

import json
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


def _workflow_payload(workflow_name: str) -> dict[str, object]:
    """Return one workflow payload as a typed string-keyed dictionary."""
    loaded_workflow: object = yaml.safe_load(_workflow_text(workflow_name))
    assert isinstance(loaded_workflow, dict), workflow_name
    workflow: dict[str, object] = {}
    for key, value in cast(dict[object, object], loaded_workflow).items():
        if isinstance(key, str):
            workflow[key] = value
    assert workflow, workflow_name
    return workflow


def _workflow_jobs(workflow_name: str) -> dict[str, object]:
    """Return one workflow's jobs mapping."""
    jobs = _as_str_object_dict(_workflow_payload(workflow_name).get("jobs"))
    assert jobs is not None, workflow_name
    return jobs


def _job_permissions(jobs: dict[str, object], job_name: str) -> dict[str, object]:
    """Return one job's permissions mapping."""
    job = _as_str_object_dict(jobs[job_name])
    assert job is not None, job_name
    permissions = _as_str_object_dict(job.get("permissions"))
    assert permissions is not None, job_name
    return permissions


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


def test_ci_gate_workflow_runs_on_pull_requests_main_push_and_emits_verdict() -> None:
    """The CI gate should run before PR merge and on merged main commits."""
    text = _workflow_text("ci-gate.yml")

    assert "on:\n  pull_request:" in text
    assert "push:\n    branches:\n      - main" in text
    assert "name: Verdict" in text
    assert "docs_parity_required" in text
    assert "dependency_review" in text
    assert "predicate-quantifier:" not in text


def test_ci_gate_dependency_review_is_blocking_and_path_selected() -> None:
    """Dependency review should block dependency changes on PRs and main pushes."""
    workflow = yaml.safe_load(_workflow_text("ci-gate.yml"))
    jobs = cast(dict[str, object], workflow["jobs"])
    dependency_review = cast(dict[str, object], jobs["dependency_review"])
    dependency_review_steps = cast(list[dict[str, object]], dependency_review["steps"])
    filters_text = _ci_gate_filters_text()

    assert dependency_review["name"] == "Dependency Review"
    assert dependency_review["if"] == "needs.classify.outputs.dependency_review == 'true'"
    assert dependency_review["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert any(
        step.get("uses")
        == "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294"
        and step.get("if") == "github.event_name == 'pull_request'"
        and step.get("continue-on-error") is True
        for step in dependency_review_steps
    )
    assert any(
        step.get("uses") == "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78"
        for step in dependency_review_steps
    )
    assert any(
        str(step.get("run", ""))
        == "uv run python3 ./tests/scripts/ci_gate.py run-dependency-audit --repo-root ."
        and step.get("if") is None
        for step in dependency_review_steps
    )
    assert "dependency_review:" in filters_text
    assert "uv.lock" in filters_text
    assert "package-lock.json" in filters_text


def test_workflow_write_permissions_are_job_scoped() -> None:
    """Workflow write tokens should be scoped to only jobs that need writes."""
    write_permissions = {
        "actions": "write",
        "attestations": "write",
        "checks": "write",
        "contents": "write",
        "deployments": "write",
        "id-token": "write",
        "issues": "write",
        "packages": "write",
        "pages": "write",
        "pull-requests": "write",
        "security-events": "write",
        "statuses": "write",
    }
    workflows_root = REPO_ROOT / ".github" / "workflows"

    for workflow_path in workflows_root.glob("*.yml"):
        workflow = _workflow_payload(workflow_path.name)
        permissions = _as_str_object_dict(workflow.get("permissions"))
        assert permissions is not None, workflow_path.name
        unexpected_writes = {
            key: value for key, value in permissions.items() if write_permissions.get(key) == value
        }
        assert unexpected_writes == {}, workflow_path.name

    ci_gate_jobs = _workflow_jobs("ci-gate.yml")
    assert _job_permissions(ci_gate_jobs, "dependency_review") == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert _job_permissions(ci_gate_jobs, "security") == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
        "security-events": "write",
    }

    security_jobs = _workflow_jobs("security.yml")
    assert _job_permissions(security_jobs, "run-security-scans") == {
        "contents": "read",
        "pull-requests": "read",
        "security-events": "write",
    }
    assert _job_permissions(security_jobs, "run-codeql-analysis") == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }

    e2e_jobs = _workflow_jobs("e2e-acceptance.yml")
    assert _job_permissions(e2e_jobs, "security-scans") == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
        "security-events": "write",
    }

    release_jobs = _workflow_jobs("release.yml")
    assert _job_permissions(release_jobs, "publish-release-artifacts") == {
        "checks": "read",
        "contents": "write",
        "attestations": "write",
        "id-token": "write",
    }

    dependency_jobs = _workflow_jobs("dependency-submission.yml")
    assert _job_permissions(dependency_jobs, "submit-dependency-snapshot") == {
        "contents": "write",
        "id-token": "write",
    }


def test_codeql_alert_age_workflow_is_report_only_and_least_privilege() -> None:
    """Alert-age governance should stay scheduled/manual and non-enforcing."""
    text = _workflow_text("codeql-alert-age.yml")
    jobs = _workflow_jobs("codeql-alert-age.yml")

    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "codeql_alert_age.py" in text
    assert "enforcement" not in text
    assert _job_permissions(jobs, "report") == {
        "contents": "read",
        "security-events": "read",
    }


def test_ci_gate_verdict_uploads_compact_json_artifact() -> None:
    """Verdict should preserve markdown summary and upload machine-readable evidence."""
    workflow = yaml.safe_load(_workflow_text("ci-gate.yml"))
    jobs = cast(dict[str, object], workflow["jobs"])
    verdict = cast(dict[str, object], jobs["verdict"])
    steps = cast(list[dict[str, object]], verdict["steps"])
    needs = cast(list[str], verdict["needs"])

    assert "dependency_review" in needs
    assert any(
        step.get("run") is not None
        and "--verdict-json-file ci-verdict.json" in str(step["run"])
        and "--dependency-review-result" in str(step["run"])
        for step in steps
    )
    assert any(
        step.get("uses") == "actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f"
        and step.get("if") == "always()"
        and step.get("with")
        == {
            "name": "ci-verdict",
            "path": "ci-verdict.json",
            "if-no-files-found": "error",
        }
        for step in steps
    )


def test_required_check_manifest_tracks_verdict_api_context() -> None:
    """Required-check policy should preserve the stable API context, not UI labels."""
    manifest = json.loads(
        (REPO_ROOT / ".github" / "required-checks.json").read_text(encoding="utf-8")
    )
    workflow = yaml.safe_load(_workflow_text("ci-gate.yml"))
    jobs = cast(dict[str, object], workflow["jobs"])
    verdict = cast(dict[str, object], jobs["verdict"])
    contexts = cast(list[dict[str, object]], manifest["requiredContexts"])
    branch_protection = cast(dict[str, object], manifest["branchProtection"])
    status_checks = cast(dict[str, object], branch_protection["requiredStatusChecks"])
    reviews = cast(dict[str, object], branch_protection["pullRequestReviews"])

    assert [context["context"] for context in contexts] == ["Verdict"]
    assert contexts[0]["jobName"] == verdict["name"] == "Verdict"
    assert contexts[0]["uiLabel"] == "CI / Verdict"
    assert status_checks["strict"] is True
    assert status_checks["contexts"] == ["Verdict"]
    assert branch_protection["allowForcePushes"] is False
    assert branch_protection["requiredConversationResolution"] is True
    assert reviews["requiredApprovingReviewCount"] == 1
    assert reviews["requireCodeOwnerReviews"] is True


def test_ci_gate_paths_filter_uses_default_quantifier() -> None:
    """Paths-filter should keep default include/exclude semantics for lane filters."""
    text = _workflow_text("ci-gate.yml")

    assert "uses: dorny/paths-filter@" in text
    assert "list-files: json" in text
    assert "predicate-quantifier:" not in text


def test_ci_gate_verdict_job_checks_out_repository_before_running_script() -> None:
    """The verdict job must checkout the repository before invoking local scripts."""
    loaded_workflow: object = yaml.safe_load(_workflow_text("ci-gate.yml"))
    assert isinstance(loaded_workflow, dict)
    workflow = cast(dict[object, object], loaded_workflow)

    jobs = _as_str_object_dict(workflow.get("jobs"))
    assert jobs is not None
    verdict = _as_str_object_dict(jobs.get("verdict"))
    assert verdict is not None

    steps_value = verdict.get("steps")
    assert isinstance(steps_value, list)

    has_checkout = False
    for step_value in cast(list[object], steps_value):
        step = _as_str_object_dict(step_value)
        if step is None:
            continue
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith("actions/checkout@"):
            has_checkout = True
            break

    assert has_checkout


def test_ci_gate_workflow_calls_reusable_heavy_lanes() -> None:
    """The CI gate should orchestrate heavy lanes through reusable workflow calls."""
    text = _workflow_text("ci-gate.yml")

    assert "uses: ./.github/workflows/harness.yml" in text
    assert "uses: ./.github/workflows/compatibility-matrix.yml" in text
    assert "uses: ./.github/workflows/memory-plugin-verification.yml" in text
    assert "uses: ./.github/workflows/fresh-host-acceptance.yml" in text
    assert "name: Fresh Host PR Fast" in text
    assert "always() &&" in text
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
    assert "./tests/scripts/hosted_docker.py wait-runtime-ready" in text
    assert "./tests/scripts/hosted_docker.py ensure-images" in text
    assert "./tests/scripts/hosted_docker.py collect-diagnostics" in text


def test_fresh_host_core_workflow_stays_thin() -> None:
    """Fresh-host core should avoid embedded programs and shell blobs."""
    text = _workflow_text("fresh-host-core.yml")

    assert "python - <<'PY'" not in text
    assert "python3 - <<'PY'" not in text
    assert "run: |" not in text
    assert ".github/scripts/fresh_host_images.py" not in text


def test_fresh_host_core_linux_job_honors_pull_tuning_inputs() -> None:
    """The Linux fresh-host job must honor per-caller docker_pull_* inputs, like macOS."""
    loaded_workflow: object = yaml.safe_load(_workflow_text("fresh-host-core.yml"))
    assert isinstance(loaded_workflow, dict)
    workflow = cast(dict[object, object], loaded_workflow)

    jobs = _as_str_object_dict(workflow.get("jobs"))
    assert jobs is not None

    # Both platform jobs must map the selected pull tuning env to the per-caller inputs.
    for job_name in ("linux-fresh-host", "macos-fresh-host"):
        job = _as_str_object_dict(jobs.get(job_name))
        assert job is not None, job_name
        job_env = _as_str_object_dict(job.get("env"))
        assert job_env is not None, job_name
        assert (
            job_env.get("FRESH_HOST_SELECTED_DOCKER_PULL_PARALLELISM")
            == "${{ inputs.docker_pull_parallelism }}"
        ), job_name
        assert (
            job_env.get("FRESH_HOST_SELECTED_DOCKER_PULL_MAX_ATTEMPTS")
            == "${{ inputs.docker_pull_max_attempts }}"
        ), job_name

    # The hard-coded top-level fallbacks must stay gone; if they return, the Linux prepull
    # would silently override the per-caller inputs again.
    workflow_env = _as_str_object_dict(workflow.get("env"))
    assert workflow_env is not None
    assert "FRESH_HOST_DOCKER_PULL_PARALLELISM" not in workflow_env
    assert "FRESH_HOST_DOCKER_PULL_MAX_ATTEMPTS" not in workflow_env


def test_fresh_host_workflow_preserves_dispatch_inputs_and_concurrency_controls() -> None:
    """Fresh-host acceptance should keep explicit tuning inputs and concurrency guards."""
    text = _workflow_text("fresh-host-acceptance.yml")

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert "docker_pull_parallelism" in text
    assert "docker_pull_max_attempts" in text
    assert "enable_package_cache" in text
    assert "fresh-host-acceptance-${{ github.workflow }}-${{ format(" in text
    assert "'{0}-{1}-{2}'" in text
    assert "inputs.docker_pull_parallelism" in text
    assert "inputs.docker_pull_max_attempts" in text
    assert "docker_pull_parallelism: ${{ inputs.docker_pull_parallelism }}" in text
    assert "docker_pull_max_attempts: ${{ inputs.docker_pull_max_attempts }}" in text
    assert "cancel-in-progress: true" in text


def test_fresh_host_core_workflow_preserves_current_macos_matrix_and_variant_support() -> None:
    """Fresh-host core should keep the current sidecars/browser-lab macOS split."""
    text = _workflow_text("fresh-host-core.yml")

    assert "macOS Fresh Host Sidecars" in text
    assert "macOS Fresh Host Browser Lab" in text
    assert "scenario_id: macos-sidecars" in text
    assert "scenario_id: macos-browser-lab" in text


def test_fresh_host_observability_and_cleanup_steps_do_not_override_scenario_results() -> None:
    """Best-effort reporting and cleanup must not turn a passing scenario red."""
    jobs = _workflow_jobs("fresh-host-core.yml")
    expected_steps = {
        "linux-fresh-host": {
            "Collect Linux fresh-host diagnostics",
            "Write Linux fresh-host summary",
        },
        "macos-fresh-host": {
            "Collect hosted macOS Docker diagnostics",
            "Collect macOS fresh-host diagnostics",
            "Write macOS fresh-host summary",
            "Clean up macOS fresh-host context",
        },
    }

    for job_name, expected_names in expected_steps.items():
        job = _as_str_object_dict(jobs.get(job_name))
        assert job is not None, job_name
        steps = job.get("steps")
        assert isinstance(steps, list), job_name
        by_name = {
            str(step.get("name")): step
            for raw_step in cast(list[object], steps)
            if (step := _as_str_object_dict(raw_step)) is not None and step.get("name")
        }
        for step_name in expected_names:
            assert by_name[step_name].get("continue-on-error") is True


def test_fresh_host_core_workflow_preserves_cache_restore_surface() -> None:
    """Fresh-host core should keep the current package cache restores."""
    text = _workflow_text("fresh-host-core.yml")

    assert "FRESH_HOST_CACHE_ROOT" in text
    assert "UV_CACHE_DIR" in text
    assert "npm_config_cache" in text
    assert "npm_config_prefer_offline" in text
    assert "Restore package download caches" in text
    assert '--github-env-file "${GITHUB_ENV}"' in text
    assert f"actions/cache/restore@{_CACHE_ACTION_NODE24_SHA}" in text
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "package-manager-cache: false" in text


def test_fresh_host_cache_warm_workflow_uses_semantic_cache_warmer() -> None:
    """Nightly cache warming should stay declarative and use the dedicated cache CLI."""
    text = _workflow_text("fresh-host-cache-warm.yml")

    assert "./tests/scripts/fresh_host_cache.py warm-packages" in text
    assert f"actions/cache/restore@{_CACHE_ACTION_NODE24_SHA}" in text
    assert f"actions/cache/save@{_CACHE_ACTION_NODE24_SHA}" in text
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "actions/cache/save@0400d5f644dc74513175e3cd8d07132dd4860809" not in text
    assert "Warm Linux Fresh Host Package Cache" in text
    assert "Warm macOS Fresh Host Package Cache" in text


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
    assert "./tests/scripts/security_workflow.py enforce-independent-review" in security
    assert "./tests/scripts/security_workflow.py verify-channels-contract --repo-root ." in security
    assert (
        "./tests/scripts/security_workflow.py run-channels-runtime-smoke --repo-root ." in security
    )
    assert "./tests/scripts/security_workflow.py run-recovery-smoke --tmp-root" in security
    assert "./tests/scripts/security_workflow.py install-gitleaks" in security
    assert "./tests/scripts/security_workflow.py install-syft" in security
    assert "./tests/scripts/security_workflow.py write-empty-sarif" in security
    assert "./tests/scripts/release_workflow.py clean-artifacts" in release
    assert "./tests/scripts/release_workflow.py runtime-readiness --repo-root ." in release
    assert "./tests/scripts/release_workflow.py verify-tag-version --tag" in release
    assert "./tests/scripts/release_workflow.py verify-tag-preflight" in release
    assert "./tests/scripts/release_workflow.py verify-artifacts" in release
    assert "./tests/scripts/release_workflow.py write-release-metadata" in release
    assert "./tests/scripts/release_workflow.py publish-github-release" in release


def test_release_workflow_publishes_manifest_checksums_and_scoped_attestations() -> None:
    """Release assets should include metadata while SBOM attestations stay scoped."""
    release = _workflow_text("release.yml")

    assert "Generate release manifest and checksums" in release
    assert "environment: release" in release
    assert "checks: read" in release
    assert "dist/*" in release
    assert "sbom.spdx.json" in release
    assert "dist/*.whl" in release
    assert "dist/*.tar.gz" in release


def test_release_break_glass_runbook_preserves_solo_maintainer_escape_hatch() -> None:
    """The shipped release runbook should document solo-safe bypass boundaries."""
    runbook = (REPO_ROOT / "platform" / "docs" / "runbooks" / "release-break-glass.md").read_text(
        encoding="utf-8"
    )

    assert "`jsugg` alone" in runbook
    assert "admin bypass" in runbook
    assert "required release-environment reviewer" in runbook
    assert "artifact manifest hash" in runbook


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


def test_codeql_config_ignores_unmaintained_and_packaged_vendor_code() -> None:
    """CodeQL should scan maintained source, not mirrors or third-party plugin code."""
    text = (REPO_ROOT / "security/codeql/codeql-config.yml").read_text(encoding="utf-8")

    assert "src/clawops/assets" in text
    assert "platform/plugins/memory-lancedb-pro" in text
    assert "platform/plugins/strongclaw-hypermemory" not in text
