"""Regression tests for operational command surfaces."""

from __future__ import annotations

import pathlib

AUTOMATION_FILES = (
    pathlib.Path("Makefile"),
    pathlib.Path("scripts/bootstrap/verify_baseline.sh"),
    pathlib.Path(".github/workflows/harness.yml"),
    pathlib.Path("scripts/bootstrap/run_harness_smoke.sh"),
)


def test_automation_surfaces_do_not_use_obsolete_harness_subcommand() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for relative_path in AUTOMATION_FILES:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "clawops harness run" not in text, f"obsolete harness CLI in {relative_path}"


def test_local_automation_reuses_shared_harness_smoke_script() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    verify_script = (repo_root / "scripts/bootstrap/verify_baseline.sh").read_text(encoding="utf-8")
    workflow = (repo_root / ".github/workflows/harness.yml").read_text(encoding="utf-8")

    assert "./scripts/bootstrap/run_harness_smoke.sh ./.runs" in makefile
    assert '"$ROOT/scripts/bootstrap/run_harness_smoke.sh" "$ROOT/.runs"' in verify_script
    assert "./scripts/bootstrap/run_harness_smoke.sh ./.runs" in workflow


def test_verify_baseline_runs_platform_static_proof() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    verify_script = (repo_root / "scripts/bootstrap/verify_baseline.sh").read_text(encoding="utf-8")

    assert '"$ROOT/scripts/bootstrap/verify_sidecars.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_observability.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_channels.sh"' in verify_script


def test_platform_verification_and_acp_scripts_use_shared_clawops_entrypoints() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    sidecars = (repo_root / "scripts/bootstrap/verify_sidecars.sh").read_text(encoding="utf-8")
    observability = (repo_root / "scripts/bootstrap/verify_observability.sh").read_text(
        encoding="utf-8"
    )
    channels = (repo_root / "scripts/bootstrap/verify_channels.sh").read_text(encoding="utf-8")
    codex = (repo_root / "scripts/workers/run_codex_session.sh").read_text(encoding="utf-8")
    reviewer = (repo_root / "scripts/workers/run_claude_review.sh").read_text(encoding="utf-8")
    fixer_loop = (repo_root / "scripts/workers/reviewer_fixer_loop.sh").read_text(encoding="utf-8")

    assert "clawops verify-platform sidecars" in sidecars
    assert "clawops verify-platform observability" in observability
    assert "clawops verify-platform channels" in channels
    assert "clawops acp-runner" in codex
    assert "clawops acp-runner" in reviewer
    assert fixer_loop.count("clawops acp-runner") == 2


def test_security_workflow_includes_plugin_path_for_codeql_javascript_scan() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    workflow = (repo_root / ".github/workflows/security.yml").read_text(encoding="utf-8")
    codeql_config = (repo_root / "security/codeql/codeql-config.yml").read_text(encoding="utf-8")

    assert "actions: read" in workflow
    assert "contents: read" in workflow
    assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in workflow
    assert "actions/checkout@v5" in workflow
    assert "languages: python,javascript" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "actions/setup-node@v6" in workflow
    assert "github/codeql-action/init@v4" in workflow
    assert "github/codeql-action/analyze@v4" in workflow
    assert 'GITLEAKS_VERSION: "8.28.0"' in workflow
    assert "gitleaks git --no-banner --no-color --exit-code 1 --log-level warn --redact" in workflow
    assert "package-manager-cache: false" in workflow
    assert "pull-requests: write" in workflow
    assert "security-events: write" in workflow
    assert "  - platform/plugins" in codeql_config


def test_github_workflows_do_not_use_deprecated_action_majors() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    deprecated_refs = (
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/setup-node@v4",
        "github/codeql-action/init@v3",
        "github/codeql-action/analyze@v3",
        "gitleaks/gitleaks-action@v2",
    )

    for workflow_path in (repo_root / ".github/workflows").glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        for deprecated_ref in deprecated_refs:
            assert deprecated_ref not in text, f"deprecated action ref in {workflow_path.name}"
