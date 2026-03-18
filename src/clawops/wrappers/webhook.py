"""Generic webhook wrapper with allowlist + operation journal."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import (
    HttpTimeouts,
    JsonHttpClient,
    RetryPolicy,
    WrapperContext,
    ensure_execution_contract,
    execute_http_operation,
    prepare_operation,
)

WEBHOOK_RETRY_POLICY = RetryPolicy.no_retry(name="webhook.post")
DEFAULT_WEBHOOK_TIMEOUTS = HttpTimeouts(connect_seconds=5.0, read_seconds=25.0)


def _decision_payload_from_operation(op: Operation) -> dict[str, str]:
    """Rebuild the policy payload for a persisted webhook operation."""
    return {
        "trust_zone": op.trust_zone,
        "action": "webhook.post",
        "category": "external_write",
        "target_kind": "webhook_url",
        "target": op.normalized_target,
    }


def invoke_webhook(
    *,
    ctx: WrapperContext,
    url: str,
    payload_body: dict[str, Any],
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """POST a JSON payload to an allowlisted webhook URL."""
    decision_payload = {
        "trust_zone": trust_zone,
        "action": "webhook.post",
        "category": "external_write",
        "target_kind": "webhook_url",
        "target": url,
    }
    prepared = prepare_operation(
        ctx=ctx,
        scope=scope,
        kind="webhook_post",
        trust_zone=trust_zone,
        normalized_target=url,
        payload=payload_body,
        decision_payload=decision_payload,
    )
    if prepared.result is not None:
        return prepared.result

    client = JsonHttpClient(timeout=DEFAULT_WEBHOOK_TIMEOUTS)
    return execute_http_operation(
        ctx=ctx,
        op=prepared.operation,
        decision=prepared.decision,
        request=lambda: client.post(
            url,
            headers={"Content-Type": "application/json"},
            json_body=payload_body,
            retry_policy=WEBHOOK_RETRY_POLICY,
        ),
    )


def execute_webhook_approved(*, ctx: WrapperContext, op_id: str) -> dict[str, Any]:
    """Execute an already-approved webhook operation."""
    op = ctx.journal.get(op_id)
    if op.kind != "webhook_post":
        raise ValueError(f"operation {op_id} is not a webhook_post")
    op, decision = ensure_execution_contract(
        ctx=ctx,
        op=op,
        decision_payload=_decision_payload_from_operation(op),
    )
    payload_body = json.loads(op.inputs_json)
    client = JsonHttpClient(timeout=DEFAULT_WEBHOOK_TIMEOUTS)
    return execute_http_operation(
        ctx=ctx,
        op=op,
        decision=decision,
        request=lambda: client.post(
            op.normalized_target,
            headers={"Content-Type": "application/json"},
            json_body=payload_body,
            retry_policy=WEBHOOK_RETRY_POLICY,
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse webhook wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--policy", type=pathlib.Path)
    parser.add_argument("--scope")
    parser.add_argument("--trust-zone")
    parser.add_argument("--url")
    parser.add_argument("--payload-file", type=pathlib.Path)
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
        result = execute_webhook_approved(ctx=ctx, op_id=args.op_id)
    else:
        for name in ("policy", "scope", "trust_zone", "url", "payload_file"):
            if getattr(args, name) is None:
                raise SystemExit(
                    f"--{name.replace('_', '-')} is required unless --execute-approved is used"
                )
        ctx = WrapperContext(
            policy_engine=PolicyEngine.from_file(args.policy),
            journal=journal,
            dry_run=args.dry_run,
        )
        payload = json.loads(args.payload_file.read_text(encoding="utf-8"))
        result = invoke_webhook(
            ctx=ctx,
            url=args.url,
            payload_body=payload,
            scope=args.scope,
            trust_zone=args.trust_zone,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
