"""GitHub wrapper for journaled comments, labels, and PR merges."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
from typing import Any, Literal, cast

from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import (
    JsonHttpClient,
    WrapperContext,
    ensure_execution_contract,
    execute_http_operation,
    prepare_operation,
)

GITHUB_ISSUE_TARGET_RE = re.compile(r"^github://(?P<repo>[^/]+/[^/]+)/issues/(?P<issue>\d+)$")
GITHUB_PULL_MERGE_TARGET_RE = re.compile(
    r"^github://(?P<repo>[^/]+/[^/]+)/pulls/(?P<pull>\d+)/merge$"
)

type MergeMethod = Literal["merge", "squash", "rebase"]
type GitHubOperation = Literal["comment", "labels", "merge"]


def _decision_payload(*, trust_zone: str, action: str, category: str, repo: str) -> dict[str, str]:
    """Build the policy payload for a GitHub side effect."""
    return {
        "trust_zone": trust_zone,
        "action": action,
        "category": category,
        "target_kind": "github_repo",
        "target": repo,
    }


def _decision_payload_from_operation(op: Operation) -> dict[str, str]:
    """Rebuild the policy payload for a persisted GitHub operation."""
    issue_match = GITHUB_ISSUE_TARGET_RE.match(op.normalized_target)
    if issue_match is not None:
        if op.kind == "github_comment":
            return _decision_payload(
                trust_zone=op.trust_zone,
                action="github.comment.create",
                category="external_write",
                repo=issue_match.group("repo"),
            )
        if op.kind == "github_issue_labels":
            return _decision_payload(
                trust_zone=op.trust_zone,
                action="github.issue.labels.add",
                category="external_write",
                repo=issue_match.group("repo"),
            )
    pull_match = GITHUB_PULL_MERGE_TARGET_RE.match(op.normalized_target)
    if pull_match is not None and op.kind == "github_pull_merge":
        return _decision_payload(
            trust_zone=op.trust_zone,
            action="github.pull_request.merge",
            category="irreversible",
            repo=pull_match.group("repo"),
        )
    raise ValueError(f"invalid GitHub target: {op.normalized_target}")


def _github_headers() -> dict[str, str]:
    """Build the shared GitHub API headers."""
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _load_json_object(value: str) -> dict[str, Any]:
    """Decode a persisted JSON object payload."""
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise TypeError("persisted GitHub operation payload must be a mapping")
    return cast(dict[str, Any], payload)


def _execute_issue_operation(
    *,
    ctx: WrapperContext,
    op: Operation,
    decision_payload: dict[str, str],
    request: Any,
) -> dict[str, Any]:
    """Run an approved issue-scoped GitHub operation."""
    updated_op, decision = ensure_execution_contract(
        ctx=ctx, op=op, decision_payload=decision_payload
    )
    return execute_http_operation(ctx=ctx, op=updated_op, decision=decision, request=request)


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
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="github_comment",
        trust_zone=trust_zone,
        normalized_target=target,
        payload={"body": body},
        decision_payload=_decision_payload(
            trust_zone=trust_zone,
            action="github.comment.create",
            category="external_write",
            repo=repo,
        ),
    )
    if prepared.result is not None:
        return prepared.result

    client = JsonHttpClient(timeout=30)
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: client.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers=_github_headers(),
            json_body={"body": body},
        ),
    )


def add_labels(
    *,
    ctx: WrapperContext,
    repo: str,
    issue_number: int,
    labels: list[str],
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """Add labels to a GitHub issue or pull request."""
    target = f"github://{repo}/issues/{issue_number}"
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="github_issue_labels",
        trust_zone=trust_zone,
        normalized_target=target,
        payload={"labels": labels},
        decision_payload=_decision_payload(
            trust_zone=trust_zone,
            action="github.issue.labels.add",
            category="external_write",
            repo=repo,
        ),
    )
    if prepared.result is not None:
        return prepared.result

    client = JsonHttpClient(timeout=30)
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: client.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/labels",
            headers=_github_headers(),
            json_body={"labels": labels},
        ),
    )


def merge_pull_request(
    *,
    ctx: WrapperContext,
    repo: str,
    pull_number: int,
    merge_method: MergeMethod,
    commit_title: str | None,
    commit_message: str | None,
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """Merge an approved GitHub pull request."""
    target = f"github://{repo}/pulls/{pull_number}/merge"
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title is not None:
        payload["commit_title"] = commit_title
    if commit_message is not None:
        payload["commit_message"] = commit_message
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="github_pull_merge",
        trust_zone=trust_zone,
        normalized_target=target,
        payload=payload,
        decision_payload=_decision_payload(
            trust_zone=trust_zone,
            action="github.pull_request.merge",
            category="irreversible",
            repo=repo,
        ),
    )
    if prepared.result is not None:
        return prepared.result

    client = JsonHttpClient(timeout=30)
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: client.put(
            f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge",
            headers=_github_headers(),
            json_body=payload,
        ),
    )


def execute_github_approved(*, ctx: WrapperContext, op_id: str) -> dict[str, Any]:
    """Execute an already-approved GitHub operation."""
    op = ctx.journal.get(op_id)
    client = JsonHttpClient(timeout=30)
    issue_match = GITHUB_ISSUE_TARGET_RE.match(op.normalized_target)
    if op.kind == "github_comment" and issue_match is not None:
        payload = _load_json_object(op.inputs_json)
        body = payload.get("body")
        if not isinstance(body, str):
            raise TypeError("github_comment payload.body must be a string")
        return _execute_issue_operation(
            ctx=ctx,
            op=op,
            decision_payload=_decision_payload_from_operation(op),
            request=lambda: client.post(
                f"https://api.github.com/repos/{issue_match.group('repo')}/issues/{issue_match.group('issue')}/comments",
                headers=_github_headers(),
                json_body={"body": body},
            ),
        )
    if op.kind == "github_issue_labels" and issue_match is not None:
        payload = _load_json_object(op.inputs_json)
        labels = payload.get("labels")
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise TypeError("github_issue_labels payload.labels must be a list of strings")
        return _execute_issue_operation(
            ctx=ctx,
            op=op,
            decision_payload=_decision_payload_from_operation(op),
            request=lambda: client.post(
                f"https://api.github.com/repos/{issue_match.group('repo')}/issues/{issue_match.group('issue')}/labels",
                headers=_github_headers(),
                json_body={"labels": list(labels)},
            ),
        )
    pull_match = GITHUB_PULL_MERGE_TARGET_RE.match(op.normalized_target)
    if op.kind == "github_pull_merge" and pull_match is not None:
        payload = _load_json_object(op.inputs_json)
        merge_method = payload.get("merge_method")
        if merge_method not in {"merge", "squash", "rebase"}:
            raise ValueError(
                "github_pull_merge payload.merge_method must be merge, squash, or rebase"
            )
        request_body: dict[str, Any] = {"merge_method": merge_method}
        if isinstance(payload.get("commit_title"), str):
            request_body["commit_title"] = payload["commit_title"]
        if isinstance(payload.get("commit_message"), str):
            request_body["commit_message"] = payload["commit_message"]
        updated_op, decision = ensure_execution_contract(
            ctx=ctx,
            op=op,
            decision_payload=_decision_payload_from_operation(op),
        )
        return execute_http_operation(
            ctx=ctx,
            op=updated_op,
            decision=decision,
            request=lambda: client.put(
                f"https://api.github.com/repos/{pull_match.group('repo')}/pulls/{pull_match.group('pull')}/merge",
                headers=_github_headers(),
                json_body=request_body,
            ),
        )
    raise ValueError(f"operation {op_id} is not an executable GitHub operation")


def execute_github_comment_approved(*, ctx: WrapperContext, op_id: str) -> dict[str, Any]:
    """Backward-compatible alias for the generic approved GitHub executor."""
    return execute_github_approved(ctx=ctx, op_id=op_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse GitHub wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--policy", type=pathlib.Path)
    parser.add_argument("--scope")
    parser.add_argument("--trust-zone")
    parser.add_argument("--repo")
    parser.add_argument("--issue-number", type=int)
    parser.add_argument("--pull-number", type=int)
    parser.add_argument("--body")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--operation", choices=("comment", "labels", "merge"), default="comment")
    parser.add_argument("--merge-method", choices=("merge", "squash", "rebase"), default="squash")
    parser.add_argument("--commit-title")
    parser.add_argument("--commit-message")
    parser.add_argument("--op-id")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _require_args(args: argparse.Namespace, *names: str) -> None:
    """Require CLI arguments for the selected GitHub wrapper operation."""
    for name in names:
        value = getattr(args, name)
        if value is None or value == []:
            raise SystemExit(
                f"--{name.replace('_', '-')} is required unless --execute-approved is used"
            )


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
        result = execute_github_approved(ctx=ctx, op_id=args.op_id)
    else:
        _require_args(args, "policy", "scope", "trust_zone", "repo")
        ctx = WrapperContext(
            policy_engine=PolicyEngine.from_file(args.policy),
            journal=journal,
            dry_run=args.dry_run,
        )
        operation = cast(GitHubOperation, args.operation)
        if operation == "comment":
            _require_args(args, "issue_number", "body")
            result = create_comment(
                ctx=ctx,
                repo=args.repo,
                issue_number=args.issue_number,
                body=args.body,
                scope=args.scope,
                trust_zone=args.trust_zone,
            )
        elif operation == "labels":
            _require_args(args, "issue_number", "label")
            result = add_labels(
                ctx=ctx,
                repo=args.repo,
                issue_number=args.issue_number,
                labels=list(args.label),
                scope=args.scope,
                trust_zone=args.trust_zone,
            )
        else:
            _require_args(args, "pull_number")
            result = merge_pull_request(
                ctx=ctx,
                repo=args.repo,
                pull_number=args.pull_number,
                merge_method=cast(MergeMethod, args.merge_method),
                commit_title=args.commit_title,
                commit_message=args.commit_message,
                scope=args.scope,
                trust_zone=args.trust_zone,
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
