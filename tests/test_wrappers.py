"""Unit tests for wrappers in dry-run mode."""

from __future__ import annotations

import pathlib

from clawops.common import write_yaml
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from clawops.wrappers.webhook import invoke_webhook


def test_webhook_wrapper_denies_non_allowlisted_target(tmp_path: pathlib.Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_yaml(
        policy_path,
        {
            "defaults": {"decision": "deny"},
            "zones": {
                "automation": {
                    "allow_actions": ["webhook.post"],
                    "allow_categories": ["external_write"],
                }
            },
            "allowlists": {"webhook_url": ["https://example.internal/hooks/deploy"]},
        },
    )
    journal = OperationJournal(tmp_path / "journal.sqlite")
    journal.init()
    ctx = WrapperContext(policy_engine=PolicyEngine.from_file(policy_path), journal=journal, dry_run=True)
    result = invoke_webhook(
        ctx=ctx,
        url="https://evil.invalid",
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )
    assert result["ok"] is False
