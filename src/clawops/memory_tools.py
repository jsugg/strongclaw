"""Migration and parity tooling for the durable-memory transition."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Final

from clawops.app_paths import scoped_state_dir
from clawops.common import ResultSummary, load_json, write_json
from clawops.hypermemory import HypermemoryEngine, default_config_path, load_config
from clawops.process_runner import run_command

MIGRATION_REPORT_VERSION: Final[int] = 1
DEFAULT_QUERY_COUNT: Final[int] = 5
DEFAULT_RESULT_LIMIT: Final[int] = 5
DEFAULT_IMPORT_TIMEOUT_SECONDS: Final[int] = 120


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateMemory:
    """Comparable view of a migrated durable-memory entry."""

    identifier: str
    text: str
    source_path: str | None
    category: str | None
    score: int

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-safe payload."""
        payload: dict[str, Any] = {
            "id": self.identifier,
            "text": self.text,
            "score": self.score,
        }
        if self.source_path is not None:
            payload["source_path"] = self.source_path
        if self.category is not None:
            payload["category"] = self.category
        return payload


def _scope_slug(scope: str) -> str:
    """Return a filesystem-safe token for a scope string."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", scope).strip("-")
    return normalized or "default-scope"


def _artifact_dir(engine: HypermemoryEngine) -> pathlib.Path:
    """Return the default directory for migration artifacts."""
    return scoped_state_dir(engine.config.workspace_root, category="memory")


def _default_import_output(engine: HypermemoryEngine, scope: str) -> pathlib.Path:
    """Return the default export artifact path."""
    return _artifact_dir(engine) / f"memory-pro-import-{_scope_slug(scope)}.json"


def _default_report_output(engine: HypermemoryEngine, scope: str, name: str) -> pathlib.Path:
    """Return the default report path for a migration/parity action."""
    return _artifact_dir(engine) / f"{name}-{_scope_slug(scope)}.json"


def _default_import_report_output(snapshot_path: pathlib.Path, scope: str) -> pathlib.Path:
    """Return the default report path for a managed memory-pro import."""
    return snapshot_path.parent / f"import-report-{_scope_slug(scope)}.json"


def _category_counts(memories: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Return per-category counts for exported memories."""
    counts = Counter(str(memory.get("category", "unknown")) for memory in memories)
    return dict(sorted(counts.items()))


def _source_path_counts(memories: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Return per-source counts for exported memories."""
    counts: Counter[str] = Counter()
    for memory in memories:
        metadata = memory.get("metadata")
        if not isinstance(metadata, dict):
            continue
        hypermemory = metadata.get("hypermemory")
        if not isinstance(hypermemory, dict):
            continue
        source_path = hypermemory.get("sourcePath")
        if isinstance(source_path, str) and source_path:
            counts[source_path] += 1
    return dict(sorted(counts.items()))


def _build_migration_summary(
    *,
    export_payload: Mapping[str, Any],
    import_output: pathlib.Path,
    report_output: pathlib.Path,
    dry_run: bool,
) -> dict[str, Any]:
    """Build a machine-readable migration summary."""
    memories_value = export_payload.get("memories")
    memories = memories_value if isinstance(memories_value, list) else []
    scope = str(export_payload.get("scope", ""))
    next_command = (
        f"openclaw memory-pro import {import_output.as_posix()} --scope {scope}"
        if not dry_run
        else None
    )
    summary: dict[str, Any] = {
        "ok": True,
        "version": MIGRATION_REPORT_VERSION,
        "scope": scope,
        "includeDaily": bool(export_payload.get("includeDaily")),
        "provider": str(export_payload.get("provider", "strongclaw-hypermemory")),
        "memoryCount": len(memories),
        "categoryCounts": _category_counts(memories),
        "sourcePathCounts": _source_path_counts(memories),
        "dryRun": dry_run,
        "report": report_output.as_posix(),
    }
    if not dry_run:
        summary["importOutput"] = import_output.as_posix()
    if next_command is not None:
        summary["nextCommand"] = next_command
    return summary


def migrate_hypermemory_to_pro(
    *,
    config_path: pathlib.Path,
    scope: str | None,
    include_daily: bool,
    output: pathlib.Path | None,
    report: pathlib.Path | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Export a durable hypermemory scope into `memory-lancedb-pro` import JSON."""
    engine = HypermemoryEngine(load_config(config_path))
    export_payload = engine.export_memory_pro_import(scope=scope, include_daily=include_daily)
    resolved_scope = str(export_payload["scope"])
    import_output = output or _default_import_output(engine, resolved_scope)
    report_output = report or _default_report_output(engine, resolved_scope, "migration-report")
    if not dry_run:
        write_json(import_output, export_payload)
    summary = _build_migration_summary(
        export_payload=export_payload,
        import_output=import_output,
        report_output=report_output,
        dry_run=dry_run,
    )
    write_json(report_output, summary)
    return summary


def import_pro_snapshot(
    *,
    input_path: pathlib.Path,
    scope: str | None,
    report: pathlib.Path | None,
    dry_run: bool,
    openclaw_bin: str,
) -> dict[str, Any]:
    """Import a migration snapshot into a live memory-pro plugin via the OpenClaw CLI."""
    payload = load_json(input_path)
    if not isinstance(payload, dict):
        raise ValueError("memory-pro import snapshot must be a JSON object")
    scope_value = payload.get("scope")
    resolved_scope = scope or (str(scope_value) if isinstance(scope_value, str) else "")
    if not resolved_scope:
        raise ValueError("memory-pro import snapshot must declare a scope or receive --scope")

    snapshot_path = input_path.expanduser().resolve()
    report_output = (
        _default_import_report_output(snapshot_path, resolved_scope)
        if report is None
        else report.expanduser().resolve()
    )
    command = [
        openclaw_bin,
        "memory-pro",
        "import",
        snapshot_path.as_posix(),
        "--scope",
        resolved_scope,
    ]
    if dry_run:
        command.append("--dry-run")

    result = run_command(command, timeout_seconds=DEFAULT_IMPORT_TIMEOUT_SECONDS)
    summary: dict[str, Any] = {
        "ok": result.ok,
        "version": MIGRATION_REPORT_VERSION,
        "scope": resolved_scope,
        "dryRun": dry_run,
        "importSnapshot": snapshot_path.as_posix(),
        "report": report_output.as_posix(),
        "command": command,
        "durationMs": result.duration_ms,
        "returnCode": result.returncode,
        "timedOut": result.timed_out,
        "failedToStart": result.failed_to_start,
    }

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        try:
            summary["response"] = json.loads(stdout)
        except json.JSONDecodeError:
            summary["stdoutExcerpt"] = stdout[:1000]
    if stderr:
        summary["stderrExcerpt"] = stderr[:1000]
    write_json(report_output, summary)
    return summary


def _default_queries(
    export_payload: Mapping[str, Any], *, limit: int = DEFAULT_QUERY_COUNT
) -> list[str]:
    """Derive deterministic verification queries from the exported payload."""
    queries: list[str] = []
    seen: set[str] = set()
    memories_value = export_payload.get("memories")
    memories = memories_value if isinstance(memories_value, list) else []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        text = str(memory.get("text", "")).strip()
        if not text:
            continue
        candidate = text.splitlines()[0].strip()
        if len(candidate) > 96:
            candidate = candidate[:96].rstrip()
        if candidate and candidate not in seen:
            queries.append(candidate)
            seen.add(candidate)
        if len(queries) >= limit:
            break
    if not queries:
        return ["memory"]
    return queries


def _normalize_candidate(
    *,
    identifier: str,
    text: str,
    source_path: str | None,
    category: str | None,
    score: int,
) -> CandidateMemory:
    """Create a comparable candidate result."""
    excerpt = text.strip().replace("\n", " ")
    if len(excerpt) > 180:
        excerpt = f"{excerpt[:177].rstrip()}..."
    return CandidateMemory(
        identifier=identifier,
        text=excerpt,
        source_path=source_path,
        category=category,
        score=score,
    )


def _tokenize_query(query: str) -> list[str]:
    """Split a query into stable casefolded search tokens."""
    return [token.casefold() for token in re.findall(r"[A-Za-z0-9_./:-]+", query)]


def _search_import_snapshot(
    export_payload: Mapping[str, Any], *, query: str, limit: int
) -> list[CandidateMemory]:
    """Search the exported import payload as a parity proxy."""
    tokens = _tokenize_query(query)
    query_casefold = query.casefold()
    memories_value = export_payload.get("memories")
    memories = memories_value if isinstance(memories_value, list) else []
    candidates: list[CandidateMemory] = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        text = str(memory.get("text", ""))
        haystack = text.casefold()
        token_hits = sum(1 for token in tokens if token and token in haystack)
        phrase_hit = int(bool(query_casefold and query_casefold in haystack))
        score = phrase_hit * 10 + token_hits
        if score <= 0:
            continue
        metadata = memory.get("metadata")
        hypermemory = metadata.get("hypermemory") if isinstance(metadata, dict) else None
        source_path = (
            str(hypermemory.get("sourcePath"))
            if isinstance(hypermemory, dict) and isinstance(hypermemory.get("sourcePath"), str)
            else None
        )
        candidates.append(
            _normalize_candidate(
                identifier=str(memory.get("id", "")),
                text=text,
                source_path=source_path,
                category=(
                    str(memory.get("category")) if memory.get("category") is not None else None
                ),
                score=score,
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.identifier))
    return candidates[:limit]


def _extract_openclaw_items(payload: Any) -> list[dict[str, Any]]:
    """Normalize diverse OpenClaw JSON payloads into a result list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "memories", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_from_openclaw_item(item: dict[str, Any], *, fallback_score: int) -> CandidateMemory:
    """Normalize one `openclaw memory-pro search` item."""
    metadata = item.get("metadata")
    hypermemory = metadata.get("hypermemory") if isinstance(metadata, dict) else None
    source_path = (
        str(hypermemory.get("sourcePath"))
        if isinstance(hypermemory, dict) and isinstance(hypermemory.get("sourcePath"), str)
        else None
    )
    text_value = item.get("text")
    if not isinstance(text_value, str):
        text_value = str(item.get("content", ""))
    category_value = item.get("category")
    raw_score = item.get("score")
    return _normalize_candidate(
        identifier=str(item.get("id", "")),
        text=text_value,
        source_path=source_path,
        category=str(category_value) if category_value is not None else None,
        score=raw_score if isinstance(raw_score, int) else fallback_score,
    )


def _search_memory_pro_cli(
    *,
    openclaw_bin: str,
    query: str,
    scope: str,
    limit: int,
) -> tuple[list[CandidateMemory], str | None]:
    """Search a live `memory-lancedb-pro` store through the OpenClaw CLI."""
    if shutil.which(openclaw_bin) is None:
        return [], f"{openclaw_bin} executable not found in PATH"
    command = [
        openclaw_bin,
        "memory-pro",
        "search",
        query,
        "--scope",
        scope,
        "--limit",
        str(limit),
        "--json",
    ]
    result = run_command(command, timeout_seconds=30)
    if not result.ok:
        error = result.stderr.strip() or result.stdout.strip() or "memory-pro search failed"
        return [], error
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [], f"unable to parse memory-pro JSON output: {exc}"
    items = _extract_openclaw_items(payload)
    candidates = [
        _candidate_from_openclaw_item(item, fallback_score=max(limit - index, 1))
        for index, item in enumerate(items, start=1)
    ]
    return candidates[:limit], None


def _old_memory_results(
    engine: HypermemoryEngine, *, query: str, scope: str, limit: int
) -> list[CandidateMemory]:
    """Search the legacy durable-memory engine."""
    hits = engine.search(query, lane="memory", scope=scope, max_results=limit)
    candidates: list[CandidateMemory] = []
    for index, hit in enumerate(hits, start=1):
        candidates.append(
            _normalize_candidate(
                identifier=hit.path,
                text=hit.snippet,
                source_path=hit.path,
                category=None,
                score=max(limit - index, 1),
            )
        )
    return candidates


def verify_pro_parity(
    *,
    config_path: pathlib.Path,
    scope: str | None,
    include_daily: bool,
    report: pathlib.Path | None,
    import_snapshot: pathlib.Path | None,
    queries: list[str],
    limit: int,
    mode: str,
    openclaw_bin: str,
) -> dict[str, Any]:
    """Compare hypermemory durable results with migrated memory-pro candidates."""
    engine = HypermemoryEngine(load_config(config_path))
    export_payload = (
        load_json(import_snapshot)
        if import_snapshot is not None
        else engine.export_memory_pro_import(scope=scope, include_daily=include_daily)
    )
    if not isinstance(export_payload, dict):
        raise ValueError("memory-pro import snapshot must be a JSON object")
    resolved_scope = str(
        export_payload.get("scope") or scope or engine.config.governance.default_scope
    )
    selected_queries = queries or _default_queries(export_payload)
    report_output = report or _default_report_output(engine, resolved_scope, "parity-report")
    query_reports: list[dict[str, Any]] = []
    parity_ok = True
    mode_used = "import_snapshot"
    errors: list[str] = []

    for query in selected_queries:
        old_results = _old_memory_results(engine, query=query, scope=resolved_scope, limit=limit)
        new_results: list[CandidateMemory]
        error: str | None = None
        if mode == "openclaw":
            mode_used = "openclaw_cli"
            new_results, error = _search_memory_pro_cli(
                openclaw_bin=openclaw_bin,
                query=query,
                scope=resolved_scope,
                limit=limit,
            )
        elif mode == "auto":
            cli_results, cli_error = _search_memory_pro_cli(
                openclaw_bin=openclaw_bin,
                query=query,
                scope=resolved_scope,
                limit=limit,
            )
            if cli_error is None:
                mode_used = "openclaw_cli"
                new_results = cli_results
            else:
                errors.append(cli_error)
                new_results = _search_import_snapshot(export_payload, query=query, limit=limit)
        else:
            new_results = _search_import_snapshot(export_payload, query=query, limit=limit)

        if error is not None:
            errors.append(error)
            parity_ok = False
        old_paths = {item.source_path for item in old_results if item.source_path}
        new_paths = {item.source_path for item in new_results if item.source_path}
        overlap = sorted(old_paths & new_paths)
        passed = (not old_paths and not new_paths) or bool(overlap)
        parity_ok = parity_ok and passed
        query_reports.append(
            {
                "query": query,
                "passed": passed,
                "oldResults": [item.to_dict() for item in old_results],
                "newResults": [item.to_dict() for item in new_results],
                "overlapPaths": overlap,
            }
        )

    report_payload: dict[str, Any] = {
        "ok": parity_ok,
        "version": MIGRATION_REPORT_VERSION,
        "scope": resolved_scope,
        "mode": mode_used,
        "queryCount": len(selected_queries),
        "queries": query_reports,
        "report": report_output.as_posix(),
        "snapshotSource": (
            import_snapshot.expanduser().resolve().as_posix()
            if import_snapshot is not None
            else None
        ),
    }
    if errors:
        report_payload["errors"] = errors
    write_json(report_output, report_payload)
    return report_payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for memory migration tooling."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser(
        "migrate-hypermemory-to-pro",
        help="Export hypermemory into memory-pro import JSON.",
    )
    migrate.add_argument("--config", type=pathlib.Path, default=default_config_path())
    migrate.add_argument("--scope")
    migrate.add_argument("--include-daily", action="store_true")
    migrate.add_argument("--output", type=pathlib.Path)
    migrate.add_argument("--report", type=pathlib.Path)
    migrate.add_argument("--dry-run", action="store_true")

    verify = sub.add_parser(
        "verify-pro-parity",
        help="Compare hypermemory durable hits with migrated memory-pro results.",
    )
    verify.add_argument("--config", type=pathlib.Path, default=default_config_path())
    verify.add_argument("--scope")
    verify.add_argument("--include-daily", action="store_true")
    verify.add_argument("--report", type=pathlib.Path)
    verify.add_argument("--import-snapshot", type=pathlib.Path)
    verify.add_argument("--query", action="append", default=[])
    verify.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    verify.add_argument(
        "--mode",
        choices=("auto", "import", "openclaw"),
        default="auto",
        help="Use the exported import snapshot, the live OpenClaw CLI, or auto-fallback.",
    )
    verify.add_argument("--openclaw-bin", default="openclaw")

    import_snapshot = sub.add_parser(
        "import-pro-snapshot",
        help="Import a migration snapshot into a live memory-pro plugin via OpenClaw.",
    )
    import_snapshot.add_argument("--input", required=True, type=pathlib.Path)
    import_snapshot.add_argument("--scope")
    import_snapshot.add_argument("--report", type=pathlib.Path)
    import_snapshot.add_argument("--dry-run", action="store_true")
    import_snapshot.add_argument("--openclaw-bin", default="openclaw")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the memory migration CLI."""
    args = parse_args(argv)
    if args.command == "migrate-hypermemory-to-pro":
        payload = migrate_hypermemory_to_pro(
            config_path=args.config,
            scope=args.scope,
            include_daily=bool(args.include_daily),
            output=args.output,
            report=args.report,
            dry_run=bool(args.dry_run),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "import-pro-snapshot":
        payload = import_pro_snapshot(
            input_path=args.input,
            scope=args.scope,
            report=args.report,
            dry_run=bool(args.dry_run),
            openclaw_bin=args.openclaw_bin,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if bool(payload.get("ok")) else 1
    if args.limit <= 0:
        result = ResultSummary(False, "limit must be positive")
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 2
    payload = verify_pro_parity(
        config_path=args.config,
        scope=args.scope,
        include_daily=bool(args.include_daily),
        report=args.report,
        import_snapshot=args.import_snapshot,
        queries=list(args.query),
        limit=args.limit,
        mode=args.mode,
        openclaw_bin=args.openclaw_bin,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if bool(payload.get("ok")) else 1
