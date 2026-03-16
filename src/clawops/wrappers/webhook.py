"""Generic webhook wrapper with allowlist + operation journal."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import JsonHttpClient, WrapperContext


def invoke_webhook(
    *,
    ctx: WrapperContext,
    url: str,
    payload_body: dict[str, Any],
    scope: str,
    trust_zone: str,
) -> dict[str, Any]:
    """POST a JSON payload to an allowlisted webhook URL."""
    payload = {
        "trust_zone": trust_zone,
        "action": "webhook.post",
        "category": "external_write",
        "target_kind": "webhook_url",
        "target": url,
    }
    decision = ctx.evaluate(payload)
    op_id = ctx.begin(scope=scope, kind="webhook_post", trust_zone=trust_zone, target=url, payload=payload_body)
    if decision.decision == "deny":
        ctx.journal.transition(op_id, "failed", error="policy denied")
        return {"ok": False, "op_id": op_id, "decision": decision.to_dict()}
    if ctx.dry_run or decision.decision == "require_approval":
        ctx.journal.transition(op_id, "approved" if decision.decision == "require_approval" else "succeeded")
        return {"ok": True, "op_id": op_id, "decision": decision.to_dict(), "dry_run": True}

    client = JsonHttpClient(timeout=30)
    ctx.journal.transition(op_id, "running")
    response = client.post(url, headers={"Content-Type": "application/json"}, json_body=payload_body)
    if response.ok:
        ctx.journal.transition(op_id, "succeeded")
    else:
        ctx.journal.transition(op_id, "failed", error=response.text[:500])
    return {"ok": response.ok, "op_id": op_id, "status": response.status_code, "body": response.text[:1000]}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse webhook wrapper CLI args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--trust-zone", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--payload-file", required=True, type=pathlib.Path)
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
