"""Command-line interface for StrongClaw hypermemory."""

from __future__ import annotations

import argparse
import json
import pathlib
from collections.abc import Mapping
from typing import Any

from clawops.common import write_json
from clawops.hypermemory.benchmark import load_benchmark_cases
from clawops.hypermemory.config import default_config_path, load_config
from clawops.hypermemory.engine import HypermemoryEngine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the hypermemory engine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=default_config_path(),
        help="Path to the hypermemory YAML config.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show index status.")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify the supported sparse+dense hypermemory backend contract.",
    )
    verify_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    index_parser = subparsers.add_parser("index", help="Rebuild the derived index.")
    index_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    search_parser = subparsers.add_parser("search", help="Search the derived memory index.")
    search_parser.add_argument("query", nargs="?", help="Search query.")
    search_parser.add_argument("--query", dest="query_flag", help="Search query.")
    search_parser.add_argument("--max-results", type=int, help="Maximum results.")
    search_parser.add_argument("--min-score", type=float, help="Minimum score threshold.")
    search_parser.add_argument("--lane", choices=("all", "memory", "corpus"), default="all")
    search_parser.add_argument("--scope", help="Exact preferred scope, e.g. project:strongclaw.")
    search_parser.add_argument(
        "--backend",
        choices=("sqlite_fts", "qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"),
        help="Override the configured search backend for this call.",
    )
    search_parser.add_argument(
        "--dense-candidate-pool",
        type=int,
        help="Override the dense candidate pool size for this call.",
    )
    search_parser.add_argument(
        "--sparse-candidate-pool",
        type=int,
        help="Override the sparse candidate pool size for this call.",
    )
    search_parser.add_argument(
        "--fusion",
        choices=("rrf", "weighted"),
        help="Override the fusion strategy for this call.",
    )
    search_parser.add_argument(
        "--explain",
        action="store_true",
        help="Include ranking explanation metadata in JSON results.",
    )
    search_parser.add_argument(
        "--include-invalidated",
        action="store_true",
        help="Include soft-invalidated rows for audit-oriented searches.",
    )
    search_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    get_parser = subparsers.add_parser("get", help="Read a canonical memory or corpus file.")
    get_parser.add_argument("path", help="Workspace-relative path returned by search.")
    get_parser.add_argument("--from", dest="from_line", type=int, help="1-based start line.")
    get_parser.add_argument("--lines", type=int, help="Number of lines to read.")
    get_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    store_parser = subparsers.add_parser("store", help="Append a durable memory entry.")
    store_parser.add_argument(
        "--type", choices=("fact", "reflection", "opinion", "entity"), required=True
    )
    store_parser.add_argument("--text", required=True)
    store_parser.add_argument("--entity")
    store_parser.add_argument("--confidence", type=float)
    store_parser.add_argument("--scope", help="Target scope, e.g. project:strongclaw.")
    store_parser.add_argument("--fact-key", help="Canonical fact slot key.")
    store_parser.add_argument("--importance", type=float, help="Initial importance score.")
    store_parser.add_argument("--tier", choices=("core", "working", "peripheral"))
    store_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    update_parser = subparsers.add_parser(
        "update", help="Replace text inside a writable memory file."
    )
    update_parser.add_argument("--path", required=True)
    update_parser.add_argument("--find", dest="find_text", required=True)
    update_parser.add_argument("--replace", dest="replace_text", required=True)
    update_parser.add_argument("--all", action="store_true", help="Replace all occurrences.")
    update_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    reflect_parser = subparsers.add_parser(
        "reflect", help="Promote retained notes into bank pages."
    )
    reflect_parser.add_argument(
        "--mode",
        choices=("safe", "propose", "apply"),
        default="safe",
        help="safe applies only configured scopes, propose never applies, apply always applies allowed scopes.",
    )
    reflect_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    capture_parser = subparsers.add_parser("capture", help="Extract durable memory from messages.")
    capture_parser.add_argument(
        "--messages", required=True, help="JSON array of [turn, role, text]."
    )
    capture_parser.add_argument("--mode", choices=("llm", "regex", "both"))
    capture_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    forget_parser = subparsers.add_parser("forget", help="Invalidate or delete a durable entry.")
    forget_parser.add_argument("--query")
    forget_parser.add_argument("--path")
    forget_parser.add_argument("--entry-text")
    forget_parser.add_argument("--hard-delete", action="store_true")
    forget_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    supersede_parser = subparsers.add_parser("supersede", help="Supersede a durable memory entry.")
    supersede_parser.add_argument("--item-id", type=int)
    supersede_parser.add_argument("--old-entry-text")
    supersede_parser.add_argument("--new-text", required=True)
    supersede_parser.add_argument(
        "--type", choices=("fact", "reflection", "opinion", "entity"), required=True
    )
    supersede_parser.add_argument("--entity")
    supersede_parser.add_argument("--confidence", type=float)
    supersede_parser.add_argument("--scope")
    supersede_parser.add_argument("--fact-key")
    supersede_parser.add_argument("--importance", type=float)
    supersede_parser.add_argument("--tier", choices=("core", "working", "peripheral"))
    supersede_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    access_parser = subparsers.add_parser("access", help="Record access counts for item ids.")
    access_parser.add_argument(
        "--item-ids", required=True, help="JSON or comma-separated item ids."
    )
    access_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    injection_parser = subparsers.add_parser(
        "record-injection",
        help="Record prompt injection counts for item ids.",
    )
    injection_parser.add_argument("--item-ids", required=True)
    injection_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    confirmation_parser = subparsers.add_parser(
        "record-confirmation",
        help="Record confirmation counts for item ids.",
    )
    confirmation_parser.add_argument("--item-ids", required=True)
    confirmation_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    bad_recall_parser = subparsers.add_parser(
        "record-bad-recall",
        help="Record bad-recall counts for item ids.",
    )
    bad_recall_parser.add_argument("--item-ids", required=True)
    bad_recall_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    lifecycle_parser = subparsers.add_parser("lifecycle", help="Run tier lifecycle evaluation.")
    lifecycle_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    list_facts_parser = subparsers.add_parser("list-facts", help="List canonical fact slots.")
    list_facts_parser.add_argument("--category")
    list_facts_parser.add_argument("--scope")
    list_facts_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    flush_parser = subparsers.add_parser(
        "flush-metadata",
        help="Flush lifecycle metadata back into canonical Markdown.",
    )
    flush_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="Run benchmark fixtures against the current strongclaw memory provider."
    )
    benchmark_parser.add_argument("--fixtures", type=pathlib.Path, required=True)
    benchmark_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    export_parser = subparsers.add_parser(
        "export-memory-pro",
        help="Export durable hypermemory entries into memory-lancedb-pro import JSON.",
    )
    export_parser.add_argument("--scope", help="Exact scope to export, e.g. project:strongclaw.")
    export_parser.add_argument(
        "--include-daily",
        action="store_true",
        help="Include retained daily-log notes in addition to durable bank/root memory files.",
    )
    export_parser.add_argument("--output", type=pathlib.Path)
    export_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the hypermemory CLI."""
    args = parse_args(argv)
    config = load_config(args.config)
    engine = HypermemoryEngine(config)
    payload: Mapping[str, Any]
    if args.command == "status":
        _print_payload(engine.status(), as_json=bool(args.json))
        return 0
    if args.command == "verify":
        payload = engine.verify()
        _print_payload(payload, as_json=bool(args.json))
        return 0 if payload.get("ok") else 1
    if args.command == "index":
        _print_payload(engine.reindex().to_dict(), as_json=bool(args.json))
        return 0
    if args.command == "search":
        query = (args.query_flag or args.query or "").strip()
        if not query:
            raise SystemExit("search query required")
        hits = engine.search(
            query,
            max_results=args.max_results,
            min_score=args.min_score,
            lane=args.lane,
            scope=args.scope,
            include_explain=bool(args.explain),
            backend=args.backend,
            dense_candidate_pool=args.dense_candidate_pool,
            sparse_candidate_pool=args.sparse_candidate_pool,
            fusion=args.fusion,
            include_invalidated=bool(args.include_invalidated),
        )
        payload = {
            "results": [hit.to_dict() for hit in hits],
            "provider": "strongclaw-hypermemory",
            "model": engine.config.embedding.model or "sqlite-fts5",
            "mode": args.lane,
            "backend": hits[0].backend if hits else (args.backend or engine.config.backend.active),
        }
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "get":
        payload = engine.read(args.path, from_line=args.from_line, lines=args.lines)
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "store":
        payload = engine.store(
            kind=args.type,
            text=args.text,
            entity=args.entity,
            confidence=args.confidence,
            scope=args.scope,
            fact_key=args.fact_key,
            importance=args.importance,
            tier=args.tier,
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "update":
        payload = engine.update(
            rel_path=args.path,
            find_text=args.find_text,
            replace_text=args.replace_text,
            replace_all=bool(args.all),
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "reflect":
        payload = engine.reflect(mode=args.mode)
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "capture":
        messages = _parse_messages(args.messages)
        payload = engine.capture(messages=messages, mode=args.mode)
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "forget":
        payload = engine.forget(
            query=args.query,
            path=args.path,
            entry_text=args.entry_text,
            hard_delete=bool(args.hard_delete),
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "supersede":
        payload = engine.supersede(
            item_id=args.item_id,
            old_entry_text=args.old_entry_text,
            new_text=args.new_text,
            kind=args.type,
            entity=args.entity,
            confidence=args.confidence,
            scope=args.scope,
            fact_key=args.fact_key,
            importance=args.importance,
            tier=args.tier,
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "access":
        payload = engine.record_access(item_ids=_parse_item_ids(args.item_ids))
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "record-injection":
        payload = engine.record_injection(item_ids=_parse_item_ids(args.item_ids))
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "record-confirmation":
        payload = engine.record_confirmation(item_ids=_parse_item_ids(args.item_ids))
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "record-bad-recall":
        payload = engine.record_bad_recall(item_ids=_parse_item_ids(args.item_ids))
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "lifecycle":
        payload = engine.run_lifecycle()
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "list-facts":
        payload = {"facts": engine.list_facts(category=args.category, scope=args.scope)}
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "flush-metadata":
        payload = engine.flush_metadata()
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "benchmark":
        payload = engine.benchmark_cases(load_benchmark_cases(args.fixtures))
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "export-memory-pro":
        payload = engine.export_memory_pro_import(
            scope=args.scope,
            include_daily=bool(args.include_daily),
        )
        if args.output is None:
            _print_payload(payload, as_json=True)
            return 0
        write_json(args.output, payload)
        summary = {
            "ok": True,
            "provider": "strongclaw-hypermemory",
            "scope": payload["scope"],
            "includeDaily": bool(args.include_daily),
            "memories": len(payload["memories"]),
            "output": args.output.as_posix(),
            "nextCommand": (
                f"openclaw memory-pro import {args.output.as_posix()} --scope {payload['scope']}"
            ),
        }
        _print_payload(summary, as_json=bool(args.json))
        return 0
    raise SystemExit(f"unsupported command: {args.command}")


def _print_payload(payload: Mapping[str, Any], *, as_json: bool) -> None:
    """Print a CLI payload in a human-readable or JSON format."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _parse_item_ids(raw_value: str) -> list[int]:
    """Parse JSON or comma-separated item ids."""
    stripped = raw_value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        if not isinstance(payload, list):
            raise ValueError("item id payload must be a list")
        return [int(item) for item in payload]
    return [int(part.strip()) for part in stripped.split(",") if part.strip()]


def _parse_messages(raw_value: str) -> list[tuple[int, str, str]]:
    """Parse capture messages from JSON."""
    payload = json.loads(raw_value)
    if not isinstance(payload, list):
        raise ValueError("messages must be a JSON list")
    messages: list[tuple[int, str, str]] = []
    for raw_item in payload:
        if not isinstance(raw_item, list) or len(raw_item) != 3:
            raise ValueError("each message must be a three-item array")
        messages.append((int(raw_item[0]), str(raw_item[1]), str(raw_item[2])))
    return messages
