"""GitHub wrapper for journaled issue comments and PR merges."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
from typing import Any

from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import (
    JsonHttpClient,
    WrapperContext,
    ensure_execution_contract,
    execute_http_operation,
    prepare_operation,
)

GITHUB_TARGET_RE = re.compile(r"^github://(?P<repo>[^/]+/[^/]+)/issues/(?P<issue>\d+)$")


def _decision_payload_from_operation(op: Operation) -> dict[str, str]:
    """Rebuild the policy payload for a persisted GitHub operation."""
    match = GITHUB_TARGET_RE.match(op.normalized_target)
    if match is None:
        raise ValueError(f"invalid GitHub target: {op.normalized_target}")
    return {
        "trust_zone": op.trust_zone,
        "action": "github.comment.create",
        "category": "external_write",
        "target_kind": "github_repo",
        "target": match.group("repo"),
    }


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
    decision_payload = {
        "trust_zone": trust_zone,
        "action": "github.comment.create",
        "category": "external_write",
        "target_kind": "github_repo",
        "target": repo,
    }
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="github_comment",
        trust_zone=trust_zone,
        normalized_target=target,
        payload={"body": body},
        decision_payload=decision_payload,
    )
    if prepared.result is not None:
        return prepared.result

    token = os.environ["GITHUB_TOKEN"]
    client = JsonHttpClient(timeout=30)
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: client.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
            json_body={"body": body},
        ),
    )


def execute_github_comment_approved(*, ctx: WrapperContext, op_id: str) -> dict[str, Any]:
    """Execute an already-approved GitHub comment operation."""
    op = ctx.journal.get(op_id)
    if op.kind != "github_comment":
        raise ValueError(f"operation {op_id} is not a github_comment")
    op, decision = ensure_execution_contract(
        ctx=ctx,
        op=op,
        decision_payload=_decision_payload_from_operation(op),
    )
    match = GITHUB_TARGET_RE.match(op.normalized_target)
    if match is None:
        raise ValueError(f"invalid GitHub target: {op.normalized_target}")
    token = os.environ["GITHUB_TOKEN"]
    body = json.loads(op.inputs_json)["body"]
    client = JsonHttpClient(timeout=30)
    return execute_http_operation(
        ctx=ctx,
        op=op,
        decision=decision,
        request=lambda: client.post(
            f"https://api.github.com/repos/{match.group('repo')}/issues/{match.group('issue')}/comments",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
            json_body={"body": body},
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse GitHub wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--policy", type=pathlib.Path)
    parser.add_argument("--scope")
    parser.add_argument("--trust-zone")
    parser.add_argument("--repo")
    parser.add_argument("--issue-number", type=int)
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
        result = execute_github_comment_approved(ctx=ctx, op_id=args.op_id)
    else:
        for name in ("policy", "scope", "trust_zone", "repo", "issue_number", "body"):
            if getattr(args, name) is None:
                raise SystemExit(
                    f"--{name.replace('_', '-')} is required unless --execute-approved is used"
                )
        ctx = WrapperContext(
            policy_engine=PolicyEngine.from_file(args.policy),
            journal=journal,
            dry_run=args.dry_run,
        )
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
