"""Jira wrapper for journaled comments and transitions."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any

import requests

from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import (
    WrapperContext,
    ensure_execution_contract,
    execute_http_operation,
    prepare_operation,
)


def _decision_payload_from_operation(op: Operation) -> dict[str, str]:
    """Rebuild the policy payload for a persisted Jira operation."""
    project_key = op.normalized_target.split("-", 1)[0]
    return {
        "trust_zone": op.trust_zone,
        "action": "jira.comment.create",
        "category": "external_write",
        "target_kind": "jira_project",
        "target": project_key,
    }


def add_comment(
    *,
    ctx: WrapperContext,
    issue_key: str,
    body: str,
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """Create a Jira issue comment."""
    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    target_project = issue_key.split("-", 1)[0]
    decision_payload = {
        "trust_zone": trust_zone,
        "action": "jira.comment.create",
        "category": "external_write",
        "target_kind": "jira_project",
        "target": target_project,
    }
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="jira_comment",
        trust_zone=trust_zone,
        normalized_target=issue_key,
        payload={"body": body},
        decision_payload=decision_payload,
    )
    if prepared.result is not None:
        return prepared.result

    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: requests.post(
            f"{base_url}/rest/api/3/issue/{issue_key}/comment",
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"body": body},
            timeout=30,
        ),
    )


def execute_jira_comment_approved(*, ctx: WrapperContext, op_id: str) -> dict[str, Any]:
    """Execute an already-approved Jira comment operation."""
    op = ctx.journal.get(op_id)
    if op.kind != "jira_comment":
        raise ValueError(f"operation {op_id} is not a jira_comment")
    op, decision = ensure_execution_contract(
        ctx=ctx,
        op=op,
        decision_payload=_decision_payload_from_operation(op),
    )
    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    body = json.loads(op.inputs_json)["body"]
    return execute_http_operation(
        ctx=ctx,
        op=op,
        decision=decision,
        request=lambda: requests.post(
            f"{base_url}/rest/api/3/issue/{op.normalized_target}/comment",
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"body": body},
            timeout=30,
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse Jira wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--policy", type=pathlib.Path)
    parser.add_argument("--scope")
    parser.add_argument("--trust-zone")
    parser.add_argument("--issue-key")
    parser.add_argument("--body")
    parser.add_argument("--op-id")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    journal = OperationJournal(args.db)
    journal.init()
    if args.execute_approved:
        if not args.op_id:
            raise SystemExit("--execute-approved requires --op-id")
        ctx = WrapperContext(
            policy_engine=(
                PolicyEngine({}) if args.policy is None else PolicyEngine.from_file(args.policy)
            ),
            journal=journal,
            dry_run=False,
        )
        result = execute_jira_comment_approved(ctx=ctx, op_id=args.op_id)
    else:
        for name in ("policy", "scope", "trust_zone", "issue_key", "body"):
            if getattr(args, name) is None:
                raise SystemExit(
                    f"--{name.replace('_', '-')} is required unless --execute-approved is used"
                )
        ctx = WrapperContext(
            policy_engine=PolicyEngine.from_file(args.policy),
            journal=journal,
            dry_run=args.dry_run,
        )
        result = add_comment(
            ctx=ctx,
            issue_key=args.issue_key,
            body=args.body,
            scope=args.scope,
            trust_zone=args.trust_zone,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
