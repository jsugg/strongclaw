"""Command-line interface for strongclaw memory v2."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from clawops.common import write_json
from clawops.memory_v2.benchmark import load_benchmark_cases
from clawops.memory_v2.config import default_config_path, load_config
from clawops.memory_v2.engine import MemoryV2Engine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the memory-v2 engine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=default_config_path(),
        help="Path to the memory-v2 YAML config.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show index status.")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

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
        "--explain",
        action="store_true",
        help="Include ranking explanation metadata in JSON results.",
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

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="Run benchmark fixtures against the current strongclaw memory provider."
    )
    benchmark_parser.add_argument("--fixtures", type=pathlib.Path, required=True)
    benchmark_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    export_parser = subparsers.add_parser(
        "export-memory-pro",
        help="Export durable memory-v2 entries into memory-lancedb-pro import JSON.",
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
    """Run the memory-v2 CLI."""
    args = parse_args(argv)
    config = load_config(args.config)
    engine = MemoryV2Engine(config)
    if args.command == "status":
        _print_payload(engine.status(), as_json=bool(args.json))
        return 0
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
        )
        payload = {
            "results": [hit.to_dict() for hit in hits],
            "provider": "strongclaw-memory-v2",
            "model": "sqlite-fts5",
            "mode": args.lane,
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
            "provider": "strongclaw-memory-v2",
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


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    """Print a CLI payload in a human-readable or JSON format."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))
