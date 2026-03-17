"""Unit tests for policy evaluation."""

from __future__ import annotations

import pathlib

from clawops.common import write_yaml
from clawops.policy_engine import PolicyEngine


def test_policy_denies_non_allowlisted_target(tmp_path: pathlib.Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_yaml(
        policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "automation": {
                    "allow_actions": ["webhook.post"],
                    "allow_categories": ["external_write"],
                }
            },
            "allowlists": {"webhook_url": ["https://example.internal/hooks/deploy"]},
        },
    )
    engine = PolicyEngine.from_file(policy_path)
    decision = engine.evaluate(
        {
            "trust_zone": "automation",
            "action": "webhook.post",
            "category": "external_write",
            "target_kind": "webhook_url",
            "target": "https://evil.invalid",
        }
    )
    assert decision.decision == "deny"


def test_policy_requires_approval_for_external_write(tmp_path: pathlib.Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_yaml(
        policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "coder": {
                    "allow_actions": ["github.comment.create"],
                    "allow_categories": ["external_write"],
                }
            },
            "approval": {"require_for_actions": ["github.comment.create"]},
        },
    )
    engine = PolicyEngine.from_file(policy_path)
    decision = engine.evaluate(
        {
            "trust_zone": "coder",
            "action": "github.comment.create",
            "category": "external_write",
            "target_kind": "github_repo",
            "target": "example/repo",
        }
    )
    assert decision.decision == "require_approval"
    assert decision.review_mode == "manual"
    assert decision.review_policy_id == "approval.actions.github.comment.create"


def test_policy_review_action_overrides_defaults(tmp_path: pathlib.Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_yaml(
        policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "reviewer": {
                    "allow_actions": ["github.pull_request.merge"],
                    "allow_categories": ["irreversible"],
                }
            },
            "approval": {"require_for_actions": ["github.pull_request.merge"]},
            "review": {
                "defaults": {"mode": "manual"},
                "actions": {
                    "github.pull_request.merge": {
                        "mode": "delegate_recommend",
                        "delegate_to": "reviewer-acp-claude",
                        "reason": "route merges through the ACP reviewer lane",
                    }
                },
            },
        },
    )
    engine = PolicyEngine.from_file(policy_path)
    decision = engine.evaluate(
        {
            "trust_zone": "reviewer",
            "action": "github.pull_request.merge",
            "category": "irreversible",
            "target_kind": "github_repo",
            "target": "example/repo",
        }
    )

    assert decision.decision == "require_approval"
    assert decision.review_mode == "delegate_recommend"
    assert decision.review_target == "reviewer-acp-claude"
    assert decision.delegate_to == "reviewer-acp-claude"
    assert decision.review_reason == "route merges through the ACP reviewer lane"
