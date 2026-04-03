"""Contract tests for the launch-readiness audit packet artifacts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final, cast

import pytest
import yaml

from tests.utils.helpers.repo import REPO_ROOT

_DEFAULT_ARTIFACT_ROOT: Final[Path] = (
    REPO_ROOT / "tests" / "fixtures" / "launch_readiness" / "audit_packet"
)
_ARTIFACT_MODE: Final[str] = (
    os.environ.get("STRONGCLAW_LAUNCH_READINESS_ARTIFACT_MODE", "fixture").strip().lower()
)
if _ARTIFACT_MODE not in {"fixture", "live"}:
    raise AssertionError("STRONGCLAW_LAUNCH_READINESS_ARTIFACT_MODE must be one of: fixture, live")


def _resolve_artifact_root() -> Path:
    """Resolve the artifact root for fixture or live packet mode."""
    if _ARTIFACT_MODE == "live":
        artifact_root_override = os.environ.get(
            "STRONGCLAW_LAUNCH_READINESS_ARTIFACT_ROOT",
            "",
        ).strip()
        if not artifact_root_override:
            raise AssertionError(
                "STRONGCLAW_LAUNCH_READINESS_ARTIFACT_ROOT is required when mode=live"
            )
        return Path(artifact_root_override).expanduser().resolve()
    return _DEFAULT_ARTIFACT_ROOT.expanduser().resolve()


_ARTIFACT_ROOT: Final[Path] = _resolve_artifact_root()
_REQUIRED_ARTIFACTS: Final[tuple[str, ...]] = (
    "launch-readiness-surface-manifest.yaml",
    "launch-readiness-workflow-matrix.yaml",
    "launch-readiness-findings.yaml",
    "launch-readiness-citation-verification.md",
    "launch-readiness-audit-report.md",
    "launch-readiness-decision-packet.md",
)
_MISSING_REQUIRED_ARTIFACTS: Final[tuple[str, ...]] = tuple(
    artifact_name
    for artifact_name in _REQUIRED_ARTIFACTS
    if not (_ARTIFACT_ROOT / artifact_name).exists()
)
pytestmark = pytest.mark.skipif(
    bool(_MISSING_REQUIRED_ARTIFACTS),
    reason=(
        "Launch-readiness packet artifacts are not available in this checkout: "
        + ", ".join(_MISSING_REQUIRED_ARTIFACTS)
    ),
)

_REQUIRED_SURFACE_IDS: Final[set[str]] = {
    "architecture_baseline",
    "production_readiness_checklist",
    "policy_engine_wrappers",
    "secrets_env_contract",
    "security_model_trust_zones",
    "host_platforms",
    "degradation_semantics",
    "acp_workers",
    "browser_lab",
    "channels",
    "context_service",
    "hypermemory",
    "observability",
    "backup_recovery",
    "plugin_inventory",
    "devflow",
    "workflow_ci_gate",
    "workflow_compatibility_matrix",
    "workflow_harness",
    "workflow_memory_plugin_verification",
    "workflow_fresh_host_acceptance",
    "workflow_fresh_host_core",
    "workflow_security",
    "workflow_nightly",
    "workflow_release",
    "workflow_dependency_submission",
    "workflow_upstream_merge_validation",
    "workflow_devflow_contract",
}

_REQUIRED_WORKFLOW_PATHS: Final[set[str]] = {
    ".github/workflows/ci-gate.yml",
    ".github/workflows/compatibility-matrix.yml",
    ".github/workflows/harness.yml",
    ".github/workflows/memory-plugin-verification.yml",
    ".github/workflows/fresh-host-acceptance.yml",
    ".github/workflows/fresh-host-core.yml",
    ".github/workflows/security.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/release.yml",
    ".github/workflows/dependency-submission.yml",
    ".github/workflows/upstream-merge-validation.yml",
    ".github/workflows/devflow-contract.yml",
}

_ALLOWED_SURFACE_TYPES: Final[set[str]] = {
    "baseline",
    "optional_exposed",
    "crosscutting",
    "workflow_evidence",
}
_ALLOWED_MAPPING_STATUS: Final[set[str]] = {"mapped", "non_launch_surface_with_rationale"}
_ALLOWED_LAUNCH_BLOCKER_DEFAULT: Final[set[str]] = {"yes", "no", "conditional"}
_ALLOWED_FINDING_STATUS: Final[set[str]] = {
    "confirmed_missing_or_broken",
    "high_risk_unproven",
    "solid_covered",
}
_ALLOWED_LAUNCH_BLOCKER_DECISION: Final[set[str]] = {
    "blocker",
    "non_blocker",
    "conditional_blocker",
}
_ALLOWED_SEVERITY: Final[set[str]] = {"critical", "high", "medium", "low"}
_ALLOWED_WORKFLOW_RELEVANCE: Final[set[str]] = {"yes", "no", "conditional"}
_CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$"
)


def _load_yaml(relative_path: str) -> dict[str, object]:
    """Load a YAML artifact as a dictionary."""
    raw_value: object = yaml.safe_load((_ARTIFACT_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict), relative_path
    return cast(dict[str, object], raw_value)


def _as_str_object_dict(value: object) -> dict[str, object] | None:
    """Return a string-keyed dictionary when valid at runtime."""
    if not isinstance(value, dict):
        return None
    validated: dict[str, object] = {}
    for key, entry in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            return None
        validated[key] = entry
    return validated


def _as_citation_list(value: object) -> list[str]:
    """Validate and return a citation list."""
    assert isinstance(value, list)
    citations: list[str] = []
    for item in cast(list[object], value):
        assert isinstance(item, str)
        citations.append(item)
    assert citations
    return citations


def _validate_citation(citation: str) -> None:
    """Validate citation syntax and file/line bounds."""
    match = _CITATION_PATTERN.match(citation)
    assert match is not None, citation

    path_text = match.group("path")
    start_line = int(match.group("start"))
    end_group = match.group("end")
    end_line = int(end_group) if end_group is not None else start_line
    assert start_line >= 1
    assert end_line >= start_line

    cited_file = REPO_ROOT / path_text
    assert cited_file.exists(), citation
    lines = cited_file.read_text(encoding="utf-8").splitlines()
    assert end_line <= len(lines), citation
    cited_lines = lines[start_line - 1 : end_line]
    assert any(line.strip() for line in cited_lines), citation


def test_required_launch_readiness_artifacts_exist() -> None:
    """The full RC8 packet artifact set must exist."""
    for artifact_name in _REQUIRED_ARTIFACTS:
        artifact_path = _ARTIFACT_ROOT / artifact_name
        assert artifact_path.exists(), artifact_name
        assert artifact_path.read_text(encoding="utf-8").strip(), artifact_name


def test_surface_manifest_schema_and_seed_coverage() -> None:
    """The manifest should satisfy schema and include all required RC8 seed surfaces."""
    manifest = _load_yaml("launch-readiness-surface-manifest.yaml")
    assert manifest.get("version") == 1

    surfaces_value = manifest.get("surfaces")
    assert isinstance(surfaces_value, list)
    surfaces = cast(list[object], surfaces_value)

    seen_surface_ids: list[str] = []
    for row_value in surfaces:
        row = _as_str_object_dict(row_value)
        assert row is not None

        surface_id = row.get("surface_id")
        assert isinstance(surface_id, str)
        seen_surface_ids.append(surface_id)

        surface_type = row.get("surface_type")
        assert isinstance(surface_type, str)
        assert surface_type in _ALLOWED_SURFACE_TYPES

        mapping_status = row.get("mapping_status")
        assert isinstance(mapping_status, str)
        assert mapping_status in _ALLOWED_MAPPING_STATUS

        launch_blocker_default = row.get("launch_blocker_default")
        assert isinstance(launch_blocker_default, str)
        assert launch_blocker_default in _ALLOWED_LAUNCH_BLOCKER_DEFAULT

        dependency_order_hint = row.get("dependency_order_hint")
        assert isinstance(dependency_order_hint, int)
        assert dependency_order_hint >= 1

        declared_sources = _as_citation_list(row.get("declared_sources"))
        for citation in declared_sources:
            _validate_citation(citation)

        if mapping_status == "non_launch_surface_with_rationale":
            rationale = row.get("non_launch_surface_rationale")
            assert isinstance(rationale, str)
            assert rationale.strip()

    assert seen_surface_ids == sorted(seen_surface_ids)
    assert _REQUIRED_SURFACE_IDS.issubset(set(seen_surface_ids))


def test_workflow_matrix_schema_and_required_workflow_coverage() -> None:
    """The workflow matrix should include each required workflow path with valid metadata."""
    matrix = _load_yaml("launch-readiness-workflow-matrix.yaml")
    assert matrix.get("version") == 1

    workflows_value = matrix.get("workflows")
    assert isinstance(workflows_value, list)
    workflows = cast(list[object], workflows_value)
    seen_workflow_paths: set[str] = set()

    for row_value in workflows:
        row = _as_str_object_dict(row_value)
        assert row is not None

        workflow_path = row.get("workflow_path")
        assert isinstance(workflow_path, str)
        seen_workflow_paths.add(workflow_path)

        relevance = row.get("launch_surface_relevance")
        assert isinstance(relevance, str)
        assert relevance in _ALLOWED_WORKFLOW_RELEVANCE

        mapping_status = row.get("mapping_status")
        assert isinstance(mapping_status, str)
        assert mapping_status in _ALLOWED_MAPPING_STATUS

        rationale = row.get("rationale")
        assert isinstance(rationale, str)
        assert rationale.strip()

        citations = _as_citation_list(row.get("citations"))
        for citation in citations:
            _validate_citation(citation)

    assert _REQUIRED_WORKFLOW_PATHS.issubset(seen_workflow_paths)


def test_findings_schema_and_cross_artifact_consistency() -> None:
    """Findings must follow schema and reference known manifest surfaces."""
    manifest = _load_yaml("launch-readiness-surface-manifest.yaml")
    manifest_rows = manifest.get("surfaces")
    assert isinstance(manifest_rows, list)
    manifest_surface_ids: set[str] = set()
    for row_value in cast(list[object], manifest_rows):
        row = _as_str_object_dict(row_value)
        assert row is not None
        surface_id = row.get("surface_id")
        assert isinstance(surface_id, str)
        manifest_surface_ids.add(surface_id)

    findings = _load_yaml("launch-readiness-findings.yaml")
    findings_value = findings.get("findings")
    assert isinstance(findings_value, list)

    seen_finding_ids: set[str] = set()
    for row_value in cast(list[object], findings_value):
        row = _as_str_object_dict(row_value)
        assert row is not None

        finding_id = row.get("finding_id")
        assert isinstance(finding_id, str)
        assert finding_id not in seen_finding_ids
        seen_finding_ids.add(finding_id)

        surface_id = row.get("surface_id")
        assert isinstance(surface_id, str)
        assert surface_id in manifest_surface_ids

        status = row.get("status")
        assert isinstance(status, str)
        assert status in _ALLOWED_FINDING_STATUS

        owner_lane = row.get("owner_lane")
        assert isinstance(owner_lane, str)
        assert owner_lane.strip()

        blocker_decision = row.get("launch_blocker_decision")
        assert isinstance(blocker_decision, str)
        assert blocker_decision in _ALLOWED_LAUNCH_BLOCKER_DECISION

        dependency_order = row.get("dependency_order")
        assert isinstance(dependency_order, int)
        assert dependency_order >= 1

        severity = row.get("severity")
        assert isinstance(severity, str)
        assert severity in _ALLOWED_SEVERITY

        citations = _as_citation_list(row.get("citations"))
        for citation in citations:
            _validate_citation(citation)

        evidence_summary = row.get("evidence_summary")
        assert isinstance(evidence_summary, str)
        assert evidence_summary.strip()


def test_report_and_decision_packet_include_release_decision_signals() -> None:
    """Report and decision packet should contain explicit launch decision language."""
    report_text = (_ARTIFACT_ROOT / "launch-readiness-audit-report.md").read_text(encoding="utf-8")
    decision_text = (_ARTIFACT_ROOT / "launch-readiness-decision-packet.md").read_text(
        encoding="utf-8"
    )

    assert "Recommendation:" in report_text
    assert "Blockers" in report_text
    assert "Residual Risks" in report_text
    assert "Assumptions" in report_text
    assert "Closure Path" in report_text

    assert "Decision:" in decision_text
    assert "Blockers" in decision_text
    assert "Required Next Actions" in decision_text
    if _ARTIFACT_MODE == "fixture":
        assert "GO for first launch" in report_text
        assert "Decision: **GO**" in decision_text
