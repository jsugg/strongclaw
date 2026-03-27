"""Contract checks for centralized CI quality gates and workflow pinning."""

from __future__ import annotations

from clawops.supply_chain import list_workflow_action_pins
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
    workflow = _workflow_text("release.yml")

    quality_gate_marker = "uv run python -m clawops supply-chain --repo-root . quality-gate"
    build_marker = "Build release artifacts"
    assert quality_gate_marker in workflow
    assert build_marker in workflow
    assert workflow.index(quality_gate_marker) < workflow.index(build_marker)


def test_quality_gate_workflows_install_shellcheck_before_gate() -> None:
    quality_gate_marker = "uv run python -m clawops supply-chain --repo-root . quality-gate"
    install_marker = "sudo apt-get install --yes shellcheck"

    for workflow_name in ("security.yml", "upstream-merge-validation.yml", "release.yml"):
        workflow = _workflow_text(workflow_name)
        assert install_marker in workflow
        assert workflow.index(install_marker) < workflow.index(quality_gate_marker)


def test_pre_commit_shellcheck_uses_system_binary() -> None:
    pre_commit_config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "https://github.com/koalaman/shellcheck-precommit" not in pre_commit_config
    assert "entry: shellcheck" in pre_commit_config
    assert "language: system" in pre_commit_config


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
    assert workflow.count("sha256sum -c -") >= 2


def test_all_workflow_actions_are_sha_pinned_and_version_tagged() -> None:
    pins = list_workflow_action_pins(REPO_ROOT)

    assert pins
    assert all(len(pin.ref) == 40 for pin in pins)
    assert all(pin.tag is not None for pin in pins)
