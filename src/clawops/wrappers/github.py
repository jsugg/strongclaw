"""GitHub wrapper for journaled issue comments and PR merges."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any

from clawops.common import load_yaml
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import JsonHttpClient, WrapperContext


def create_comment(
    *,
    ctx: WrapperContext,
    repo: str,
    issue_number: int,
    body: str,
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """Create a GitHub issue or PR comment."""
    target = f"github://{repo}/issues/{issue_number}"
    payload = {
        "trust_zone": trust_zone,
        "action": "github.comment.create",
        "category": "external_write",
        "target_kind": "github_repo",
        "target": repo,
    }
    decision = ctx.evaluate(payload)
    op_id = ctx.begin(scope=scope, kind="github_comment", trust_zone=trust_zone, target=target, payload={"body": body})
    if decision.decision == "deny":
        ctx.journal.transition(op_id, "failed", error="policy denied")
        return {"ok": False, "op_id": op_id, "decision": decision.to_dict()}
    if ctx.dry_run or decision.decision == "require_approval":
        ctx.journal.transition(op_id, "approved" if decision.decision == "require_approval" else "succeeded")
        return {"ok": True, "op_id": op_id, "decision": decision.to_dict(), "dry_run": True}

    token = os.environ["GITHUB_TOKEN"]
    client = JsonHttpClient(timeout=30)
    ctx.journal.transition(op_id, "running")
    response = client.post(
        f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
        json_body={"body": body},
    )
    if response.ok:
        ctx.journal.transition(op_id, "succeeded")
    else:
        ctx.journal.transition(op_id, "failed", error=response.text[:500])
    return {"ok": response.ok, "op_id": op_id, "status": response.status_code, "body": response.text[:1000]}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse GitHub wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--trust-zone", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue-number", required=True, type=int)
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
    result = create_comment(
        ctx=ctx,
        repo=args.repo,
        issue_number=args.issue_number,
        body=args.body,
        scope=args.scope,
        trust_zone=args.trust_zone,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
