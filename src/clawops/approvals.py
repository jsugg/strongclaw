"""Operator-facing approval and review queue commands."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
from typing import Any

from clawops.common import load_json, write_json
from clawops.op_journal import OperationJournal


def _load_payload_file(path: pathlib.Path | None) -> dict[str, Any] | None:
    """Load an optional JSON payload file."""
    if path is None:
        return None
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise TypeError("review payload file must decode to a JSON object")
    return payload


def _print_or_write(payload: object, *, output: pathlib.Path | None = None) -> None:
    """Emit JSON to stdout and optionally write it to disk."""
    if output is not None:
        write_json(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse approval CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    queue_parser = sub.add_parser("queue")
    queue_parser.add_argument("--db", required=True, type=pathlib.Path)
    queue_parser.add_argument("--output", type=pathlib.Path)

    show_parser = sub.add_parser("show")
    show_parser.add_argument("--db", required=True, type=pathlib.Path)
    show_parser.add_argument("--op-id", required=True)

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--db", required=True, type=pathlib.Path)
    approve_parser.add_argument("--op-id", required=True)
    approve_parser.add_argument("--approved-by", required=True)
    approve_parser.add_argument("--note")
    approve_parser.add_argument("--artifact-path", type=pathlib.Path)
    approve_parser.add_argument("--payload-file", type=pathlib.Path)

    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("--db", required=True, type=pathlib.Path)
    reject_parser.add_argument("--op-id", required=True)
    reject_parser.add_argument("--reviewed-by", required=True)
    reject_parser.add_argument("--note")
    reject_parser.add_argument("--artifact-path", type=pathlib.Path)
    reject_parser.add_argument("--payload-file", type=pathlib.Path)

    delegate_parser = sub.add_parser("delegate")
    delegate_parser.add_argument("--db", required=True, type=pathlib.Path)
    delegate_parser.add_argument("--op-id", required=True)
    delegate_parser.add_argument("--reviewed-by", required=True)
    delegate_parser.add_argument("--to", required=True)
    delegate_parser.add_argument("--note")
    delegate_parser.add_argument("--artifact-path", type=pathlib.Path)
    delegate_parser.add_argument("--payload-file", type=pathlib.Path)

    ingest_parser = sub.add_parser("ingest-review")
    ingest_parser.add_argument("--db", required=True, type=pathlib.Path)
    ingest_parser.add_argument("--op-id", required=True)
    ingest_parser.add_argument("--reviewed-by", required=True)
    ingest_parser.add_argument("--decision", required=True, choices=("allow", "deny"))
    ingest_parser.add_argument("--note")
    ingest_parser.add_argument("--artifact-path", type=pathlib.Path)
    ingest_parser.add_argument("--payload-file", type=pathlib.Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    journal = OperationJournal(args.db)
    journal.init()

    if args.command == "queue":
        queue_payload = [dataclasses.asdict(op) for op in journal.queue()]
        _print_or_write(queue_payload, output=args.output)
        return 0

    if args.command == "show":
        print(json.dumps(dataclasses.asdict(journal.get(args.op_id)), indent=2, sort_keys=True))
        return 0

    review_payload = _load_payload_file(getattr(args, "payload_file", None))
    artifact_path = getattr(args, "artifact_path", None)
    if args.command == "approve":
        operation = journal.approve(
            args.op_id,
            approved_by=args.approved_by,
            note=args.note,
            review_artifact_path=artifact_path,
            review_payload=review_payload,
        )
    elif args.command == "reject":
        operation = journal.reject(
            args.op_id,
            reviewed_by=args.reviewed_by,
            note=args.note,
            review_artifact_path=artifact_path,
            review_payload=review_payload,
        )
    elif args.command == "delegate":
        operation = journal.delegate(
            args.op_id,
            reviewed_by=args.reviewed_by,
            delegate_to=args.to,
            note=args.note,
            review_artifact_path=artifact_path,
            review_payload=review_payload,
        )
    elif args.command == "ingest-review":
        operation = journal.ingest_review(
            args.op_id,
            reviewed_by=args.reviewed_by,
            decision=args.decision,
            note=args.note,
            review_artifact_path=artifact_path,
            review_payload=review_payload,
        )
    else:
        raise AssertionError("unreachable")

    print(json.dumps(dataclasses.asdict(operation), indent=2, sort_keys=True))
    return 0
