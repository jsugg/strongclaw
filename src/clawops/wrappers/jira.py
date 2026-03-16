"""Jira wrapper for journaled comments and transitions."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any

import requests

from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext


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
    payload = {
        "trust_zone": trust_zone,
        "action": "jira.comment.create",
        "category": "external_write",
        "target_kind": "jira_project",
        "target": target_project,
    }
    decision = ctx.evaluate(payload)
    op_id = ctx.begin(scope=scope, kind="jira_comment", trust_zone=trust_zone, target=issue_key, payload={"body": body})
    if decision.decision == "deny":
        ctx.journal.transition(op_id, "failed", error="policy denied")
        return {"ok": False, "op_id": op_id, "decision": decision.to_dict()}
    if ctx.dry_run or decision.decision == "require_approval":
        ctx.journal.transition(op_id, "approved" if decision.decision == "require_approval" else "succeeded")
        return {"ok": True, "op_id": op_id, "decision": decision.to_dict(), "dry_run": True}

    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    ctx.journal.transition(op_id, "running")
    response = requests.post(
        f"{base_url}/rest/api/3/issue/{issue_key}/comment",
        auth=(email, token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"body": body},
        timeout=30,
    )
    if response.ok:
        ctx.journal.transition(op_id, "succeeded")
    else:
        ctx.journal.transition(op_id, "failed", error=response.text[:500])
    return {"ok": response.ok, "op_id": op_id, "status": response.status_code, "body": response.text[:1000]}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse Jira wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--trust-zone", required=True)
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    ctx = WrapperContext(
        policy_engine=PolicyEngine.from_file(args.policy),
        journal=OperationJournal(args.db),
        dry_run=args.dry_run,
    )
    ctx.journal.init()
    result = add_comment(
        ctx=ctx,
        issue_key=args.issue_key,
        body=args.body,
        scope=args.scope,
        trust_zone=args.trust_zone,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
