"""Unit tests for policy evaluation."""

from __future__ import annotations

import pathlib
import json

from clawops.common import write_json, write_yaml
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
            "zones": {"coder": {"allow_actions": ["github.comment.create"], "allow_categories": ["external_write"]}},
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
