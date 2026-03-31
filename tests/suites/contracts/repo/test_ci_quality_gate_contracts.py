"""Contract checks for centralized CI quality gates and workflow pinning."""

from __future__ import annotations

from typing import cast

import yaml

from clawops.supply_chain import QUALITY_GATE_COMMANDS, list_workflow_action_pins
from tests.utils.helpers.repo import REPO_ROOT


def _workflow_text(workflow_name: str) -> str:
    """Return the requested workflow text."""
    return (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")


def test_security_and_upstream_workflows_use_central_quality_gate() -> None:
    for workflow_name in ("security.yml", "upstream-merge-validation.yml"):
        workflow = _workflow_text(workflow_name)
        assert "uv run python -m clawops supply-chain --repo-root . quality-gate" in workflow
        assert "uv run pre-commit run actionlint --all-files" not in workflow
        assert "uv run pre-commit run shellcheck --all-files" not in workflow
        assert "uv run pytest -q --junitxml=pytest.xml" not in workflow
        assert "python -m compileall -q src tests" not in workflow


def test_release_workflow_runs_quality_gate_before_publish() -> None:
    workflow = yaml.safe_load(_workflow_text("release.yml"))
    jobs = workflow["jobs"]
    quality_gate_job = jobs["release-quality-gate"]
    publish_job = jobs["publish-release-artifacts"]
    quality_gate_steps = cast(list[dict[str, object]], quality_gate_job["steps"])

    assert any(
        step.get("run") == "uv run python -m clawops supply-chain --repo-root . quality-gate"
        for step in quality_gate_steps
    )
    assert "release-quality-gate" in publish_job["needs"]


def test_quality_gate_workflows_install_shellcheck_before_gate() -> None:
    quality_gate_marker = "uv run python -m clawops supply-chain --repo-root . quality-gate"
    install_marker = "sudo apt-get install --yes shellcheck"

    for workflow_name in ("security.yml", "upstream-merge-validation.yml", "release.yml"):
        workflow = _workflow_text(workflow_name)
        assert install_marker in workflow
        assert workflow.index(install_marker) < workflow.index(quality_gate_marker)


def test_quality_gate_enforces_coverage_thresholds() -> None:
    assert (
        "python3",
        "./tests/scripts/security_workflow.py",
        "enforce-coverage-thresholds",
        "--coverage-file",
        "coverage.xml",
    ) in QUALITY_GATE_COMMANDS


def test_pre_commit_shellcheck_uses_system_binary() -> None:
    pre_commit_config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "https://github.com/koalaman/shellcheck-precommit" not in pre_commit_config
    assert "entry: shellcheck" in pre_commit_config
    assert "language: system" in pre_commit_config


def test_pre_commit_python_type_hooks_only_run_for_python_changes() -> None:
    payload = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    local_repo = next(repo for repo in payload["repos"] if repo["repo"] == "local")
    hooks = {hook["id"]: hook for hook in local_repo["hooks"]}

    for hook_id in ("pyright", "mypy"):
        assert hooks[hook_id]["pass_filenames"] is False
        assert hooks[hook_id]["types_or"] == ["python", "pyi"]


def test_security_workflow_verifies_downloaded_tool_archives() -> None:
    workflow = _workflow_text("security.yml")

    assert (
        'GITLEAKS_SHA256: "a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb"'
        in workflow
    )
    assert (
        'SYFT_SHA256: "1d3cc98b13ce3dfb6083ef42f64f1033e40d7dea292e8ea85ed1cf88efb2f542"'
        in workflow
    )
    assert "./tests/scripts/security_workflow.py install-gitleaks" in workflow
    assert "./tests/scripts/security_workflow.py install-syft" in workflow


def test_security_workflow_uses_cli_semgrep_instead_of_docker_action() -> None:
    workflow = _workflow_text("security.yml")

    assert "returntocorp/semgrep-action" not in workflow
    assert 'python3 -m pip install --disable-pip-version-check "semgrep==1.156.0"' in workflow
    assert "semgrep scan --config security/semgrep/semgrep.yml --error ." in workflow


def test_semgrep_rules_cover_python_repo_risk_surfaces() -> None:
    payload = yaml.safe_load((REPO_ROOT / "security" / "semgrep" / "semgrep.yml").read_text())
    rule_ids = {rule["id"] for rule in payload["rules"]}

    assert "python-unsafe-tarfile-extractall" in rule_ids
    assert "python-tar-member-path-join" in rule_ids
    assert "python-subprocess-shell-true" in rule_ids
    assert "python-unsafe-deserialization" in rule_ids


def test_memory_plugin_qdrant_workflow_uses_pinned_ghcr_service_image() -> None:
    workflow = _workflow_text("memory-plugin-verification.yml")

    assert "image: qdrant/qdrant" not in workflow
    assert (
        "ghcr.io/qdrant/qdrant/qdrant:v1.15.5@sha256:"
        "21934642fbdc0010b3df46ab214a755fda7a4631a58beec89b050baca4c78311"
    ) in workflow


def test_all_workflow_actions_are_sha_pinned_and_version_tagged() -> None:
    pins = list_workflow_action_pins(REPO_ROOT)

    assert pins
    assert all(len(pin.ref) == 40 for pin in pins)
    assert all(pin.tag is not None for pin in pins)
