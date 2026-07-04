"""Unit coverage for required-check policy audits."""

from __future__ import annotations

from tests.utils.helpers._ci_workflows.required_checks import (
    audit_branch_protection,
    load_required_check_manifest,
)
from tests.utils.helpers.repo import REPO_ROOT


def test_required_check_policy_accepts_current_solo_safe_branch_snapshot() -> None:
    """The manifest should match the exported post-hardening branch state."""
    manifest = load_required_check_manifest(REPO_ROOT / ".github" / "required-checks.json")
    branch_protection: dict[str, object] = {
        "required_status_checks": {
            "strict": True,
            "contexts": ["Verdict"],
            "checks": [{"context": "Verdict", "app_id": 15368}],
        },
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
        "enforce_admins": {"enabled": False},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "require_code_owner_reviews": True,
        },
    }

    assert (
        audit_branch_protection(
            manifest=manifest,
            branch_protection=branch_protection,
        )
        == []
    )


def test_required_check_policy_flags_context_drift() -> None:
    """The audit should catch stale UI labels being enforced as API contexts."""
    manifest = load_required_check_manifest(REPO_ROOT / ".github" / "required-checks.json")
    branch_protection: dict[str, object] = {
        "required_status_checks": {
            "strict": True,
            "contexts": ["CI / Verdict"],
            "checks": [{"context": "CI / Verdict", "app_id": 15368}],
        },
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
        "enforce_admins": {"enabled": False},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "require_code_owner_reviews": True,
        },
    }

    findings = audit_branch_protection(manifest=manifest, branch_protection=branch_protection)

    assert any("contexts mismatch" in finding for finding in findings)
