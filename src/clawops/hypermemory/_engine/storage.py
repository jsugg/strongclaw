"""Canonical memory mutation and maintenance methods for StrongClaw hypermemory."""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from clawops.common import ensure_parent
from clawops.hypermemory.capture import (
    CaptureCandidate,
)
from clawops.hypermemory.config import resolve_under_workspace
from clawops.hypermemory.defaults import (
    BANK_HEADERS,
    FACT_KEY_INFERENCE_RULES,
    FACT_QUERY_RULES,
    MEMORY_PRO_IMPORTANCE_MAP,
    WRITABLE_PREFIXES,
)
from clawops.hypermemory.governance import should_auto_apply, validate_scope
from clawops.hypermemory.models import (
    EvidenceEntry,
    FactCategory,
    ProposalRecord,
    ReflectionMode,
    SearchHit,
    Tier,
)
from clawops.hypermemory.noise import is_noise
from clawops.hypermemory.parser import parse_typed_entry
from clawops.hypermemory.utils import sha256, slugify


def export_memory_pro_import(
    self,
    *,
    scope: str | None = None,
    include_daily: bool = False,
    auto_index: bool = True,
) -> dict[str, Any]:
    """Export durable hypermemory entries as `memory-lancedb-pro` import JSON.

    The vendored `memory-lancedb-pro` CLI imports one target scope at a time,
    so this export stays scope-specific and preserves the original source
    coordinates in metadata for auditability.
    """
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for export")
    return canonical_store.export_memory_pro_import(
        scope=scope,
        include_daily=include_daily,
        auto_index=auto_index,
    )


def store(
    self,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    text: str,
    entity: str | None = None,
    confidence: float | None = None,
    scope: str | None = None,
    fact_key: str | None = None,
    importance: float | None = None,
    tier: Tier | None = None,
    supersedes: str | None = None,
    _skip_preindex_sync: bool = False,
    _skip_preflush_on_reindex: bool = False,
    _skip_dedup: bool = False,
) -> dict[str, Any]:
    """Append a durable memory entry to the appropriate canonical Markdown file."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for store")
    return canonical_store.store(
        kind=kind,
        text=text,
        entity=entity,
        confidence=confidence,
        scope=scope,
        fact_key=fact_key,
        importance=importance,
        tier=tier,
        supersedes=supersedes,
        _skip_preindex_sync=_skip_preindex_sync,
        _skip_preflush_on_reindex=_skip_preflush_on_reindex,
        _skip_dedup=_skip_dedup,
    )


def update(
    self,
    *,
    rel_path: str,
    find_text: str,
    replace_text: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Replace text inside a writable memory file."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for update")
    return canonical_store.update(
        rel_path=rel_path,
        find_text=find_text,
        replace_text=replace_text,
        replace_all=replace_all,
    )


def reflect(self, *, mode: ReflectionMode = "safe") -> dict[str, Any]:
    """Promote retained daily-log entries into durable bank pages via proposals."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for reflect")
    return canonical_store.reflect(mode=mode)


def capture(
    self,
    *,
    messages: Sequence[tuple[int, str, str]],
    mode: Literal["llm", "regex", "both"] | None = None,
) -> dict[str, Any]:
    """Extract and store durable memory candidates from conversation messages."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for capture")
    return canonical_store.capture(messages=messages, mode=mode)


def forget(
    self,
    *,
    query: str | None = None,
    path: str | None = None,
    entry_text: str | None = None,
    hard_delete: bool = False,
) -> dict[str, Any]:
    """Invalidate or delete a durable memory entry."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for forget")
    return canonical_store.forget(
        query=query,
        path=path,
        entry_text=entry_text,
        hard_delete=hard_delete,
    )


def supersede(
    self,
    *,
    item_id: int | None = None,
    old_entry_text: str | None = None,
    new_text: str,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    entity: str | None = None,
    confidence: float | None = None,
    scope: str | None = None,
    fact_key: str | None = None,
    importance: float | None = None,
    tier: Tier | None = None,
) -> dict[str, Any]:
    """Store a new entry that supersedes an existing durable entry."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for supersede")
    return canonical_store.supersede(
        item_id=item_id,
        old_entry_text=old_entry_text,
        new_text=new_text,
        kind=kind,
        entity=entity,
        confidence=confidence,
        scope=scope,
        fact_key=fact_key,
        importance=importance,
        tier=tier,
    )


def record_access(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
    """Record retrieval access for durable typed memory items."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for record_access")
    return canonical_store.record_access(item_ids=item_ids)


def record_injection(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
    """Record that items were auto-injected into a prompt."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for record_injection")
    return canonical_store.record_injection(item_ids=item_ids)


def record_confirmation(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
    """Record that recalled items were confirmed useful."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for record_confirmation")
    return canonical_store.record_confirmation(item_ids=item_ids)


def record_bad_recall(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
    """Record that recalled items were contradicted or unhelpful."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for record_bad_recall")
    return canonical_store.record_bad_recall(item_ids=item_ids)


def flush_metadata(self) -> dict[str, Any]:
    """Flush lifecycle metadata from SQLite rows back into canonical Markdown."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for flush_metadata")
    return canonical_store.flush_metadata()


def run_lifecycle(self) -> dict[str, Any]:
    """Evaluate lifecycle scores and promote or demote tiers."""
    canonical_store = getattr(self, "canonical_store", None)
    if canonical_store is None:
        raise RuntimeError("canonical_store is required for run_lifecycle")
    return canonical_store.run_lifecycle()


def get_fact(
    self,
    fact_key: str,
    *,
    conn: sqlite3.Connection | None = None,
    scope: str | None = None,
) -> SearchHit | None:
    """Return the current active value for a canonical fact slot."""
    normalized_key = fact_key.strip()
    if not normalized_key:
        return None
    owns_connection = conn is None
    active_conn = self.connect() if conn is None else conn
    try:
        row = active_conn.execute(
            """
                SELECT
                    search_items.id,
                    search_items.rel_path,
                    search_items.start_line,
                    search_items.end_line,
                    search_items.snippet,
                    search_items.lane,
                    search_items.item_type,
                    search_items.confidence,
                    search_items.scope,
                    search_items.evidence_count,
                    search_items.contradiction_count,
                    search_items.entities_json,
                    search_items.modified_at,
                    search_items.importance,
                    search_items.tier,
                    search_items.access_count,
                    search_items.last_access_date,
                    search_items.injected_count,
                    search_items.confirmed_count,
                    search_items.bad_recall_count,
                    search_items.fact_key,
                    search_items.invalidated_at,
                    search_items.supersedes
                FROM fact_registry
                JOIN search_items ON search_items.id = fact_registry.current_item_id
                WHERE fact_registry.fact_key = ?
                """,
            (normalized_key,),
        ).fetchone()
        if row is None:
            return None
        if scope is not None and str(row["scope"]) not in {scope, "global"}:
            return None
        return self._row_to_search_hit(row)
    finally:
        if owns_connection:
            active_conn.close()


def list_facts(
    self,
    *,
    category: str | None = None,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """List current canonical facts from the registry."""
    with self.connect() as conn:
        params: list[Any] = []
        category_clause = ""
        if category:
            category_clause = "AND fact_registry.category = ?"
            params.append(category)
        rows = conn.execute(
            f"""
                SELECT
                    fact_registry.fact_key,
                    fact_registry.category,
                    fact_registry.version_count,
                    fact_registry.history_json,
                    search_items.id,
                    search_items.rel_path,
                    search_items.start_line,
                    search_items.end_line,
                    search_items.snippet,
                    search_items.scope,
                    search_items.fact_key,
                    search_items.supersedes
                FROM fact_registry
                JOIN search_items ON search_items.id = fact_registry.current_item_id
                WHERE 1 = 1
                  {category_clause}
                ORDER BY fact_registry.fact_key
                """,
            params,
        ).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            if scope is not None and str(row["scope"]) not in {scope, "global"}:
                continue
            payload.append(
                {
                    "factKey": str(row["fact_key"]),
                    "category": str(row["category"]),
                    "versionCount": int(row["version_count"]),
                    "history": json.loads(str(row["history_json"])),
                    "item": self._row_to_search_hit(row).to_dict(),
                }
            )
        return payload


def benchmark_cases(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run simple benchmark cases against the current engine."""
    results: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        name = str(case["name"])
        query = str(case["query"])
        expected_paths = {str(entry) for entry in case.get("expectedPaths", [])}
        hits = self.search(
            query,
            max_results=int(case.get("maxResults", self.config.default_max_results)),
            lane=str(case.get("lane", "all")),  # type: ignore[arg-type]
        )
        actual_paths = {hit.path for hit in hits}
        hit = expected_paths.issubset(actual_paths)
        if hit:
            passed += 1
        results.append(
            {
                "name": name,
                "query": query,
                "expectedPaths": sorted(expected_paths),
                "actualPaths": sorted(actual_paths),
                "passed": hit,
            }
        )
    return {
        "provider": "strongclaw-hypermemory",
        "cases": results,
        "passed": passed,
        "total": len(results),
    }


def _is_noise(self, text: str) -> bool:
    """Return whether *text* should be rejected as durable noise."""
    return self.config.noise.enabled and is_noise(text, config=self.config.noise)


def _passes_admission(self, candidate: CaptureCandidate) -> bool:
    """Return whether a capture candidate clears optional admission control."""
    if not self.config.admission.enabled:
        return True
    prior = float(self.config.admission.type_priors.get(candidate.kind, 0.0))
    if prior < self.config.admission.min_confidence:
        return False
    if candidate.confidence is None:
        return True
    return candidate.confidence >= self.config.admission.min_confidence


def _normalize_tier(self, value: str | Tier | None) -> Tier:
    """Return a validated lifecycle tier."""
    if isinstance(value, str) and value.strip().lower() in {"core", "working", "peripheral"}:
        return cast(Tier, value.strip().lower())
    return "working"


def _infer_fact_key(
    self,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    text: str,
) -> str | None:
    """Infer a canonical fact key from one durable entry."""
    del kind
    normalized = text.strip()
    for pattern, fixed_key in FACT_KEY_INFERENCE_RULES:
        match = pattern.search(normalized)
        if not match:
            continue
        if fixed_key is not None:
            return fixed_key
        subject = re.sub(r"[^a-z0-9]+", "-", match.group(1).strip().lower()).strip("-")
        if subject:
            return f"decision:{subject}"
    return None


def _infer_query_fact_key(self, query: str) -> str | None:
    """Infer a fact key from a user query."""
    stripped = query.strip()
    if re.fullmatch(r"[a-z]+:[a-z0-9_-]+", stripped):
        return stripped
    for pattern, fact_key in FACT_QUERY_RULES:
        if pattern.search(stripped):
            return fact_key
    return None


def _fact_category(self, fact_key: str) -> FactCategory:
    """Map a fact key to its registry category."""
    if fact_key.startswith("user:"):
        return "profile"
    if fact_key.startswith("pref:"):
        return "preference"
    if fact_key.startswith("decision:"):
        return "decision"
    return "entity"


def _entry_hash_prefix(self, entry_line: str) -> str:
    """Return a short, stable reference for a canonical entry line."""
    return sha256(entry_line.strip())[:8]


def _search_hit_text(self, hit: SearchHit) -> str:
    """Extract the entry body text from a search hit snippet."""
    return self._typed_entry_text(hit.snippet)


def _typed_entry_text(self, entry_line: str) -> str:
    """Extract the human-authored body from one typed entry line."""
    stripped = entry_line.strip()
    if stripped.startswith(("- ", "* ")):
        stripped = stripped[2:].strip()
    if ": " in stripped:
        return stripped.split(": ", 1)[1].strip()
    return stripped


def _resolve_entry_reference(
    self,
    *,
    item_id: int | None = None,
    query: str | None = None,
    path: str | None = None,
    entry_text: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a mutable durable entry by item id, query, or file/text match."""
    if item_id is not None:
        with self.connect() as conn:
            return self._entry_reference_from_item_id(conn, item_id=item_id)
    if query:
        hits = self.search(query, max_results=1, lane="memory")
        if hits and hits[0].score >= 0.9 and hits[0].item_id is not None:
            with self.connect() as conn:
                return self._entry_reference_from_item_id(conn, item_id=hits[0].item_id)
    if path and entry_text:
        return self._entry_reference_from_text(path=path, entry_text=entry_text)
    if entry_text:
        writable_paths: list[str] = []
        bank_dir = self.config.workspace_root / self.config.bank_dir
        if bank_dir.exists():
            writable_paths.extend(
                resolve_under_workspace(self.config.workspace_root, candidate)
                for candidate in sorted(bank_dir.rglob("*.md"))
            )
        for rel_path in writable_paths:
            target = self._entry_reference_from_text(path=rel_path, entry_text=entry_text)
            if target is not None:
                return target
    return None


def _entry_reference_from_item_id(
    self,
    conn: sqlite3.Connection,
    *,
    item_id: int,
) -> dict[str, Any] | None:
    """Load a mutable durable entry reference from one indexed row id."""
    row = conn.execute(
        """
            SELECT id, rel_path, start_line, snippet, scope
            FROM search_items
            WHERE id = ?
            """,
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "item_id": int(row["id"]),
        "rel_path": str(row["rel_path"]),
        "start_line": int(row["start_line"]),
        "entry_line": str(row["snippet"]),
        "scope": str(row["scope"]),
    }


def _entry_reference_from_text(self, *, path: str, entry_text: str) -> dict[str, Any] | None:
    """Resolve a mutable entry by exact body text inside one file."""
    target = self._resolve_writable_path(path)
    if not target.exists():
        return None
    for line_number, raw_line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        bullet_match = re.match(r"^(?P<prefix>\s*[-*]\s+)(?P<body>.+?)\s*$", raw_line)
        if bullet_match is None:
            continue
        body = bullet_match.group("body").strip()
        if self._typed_entry_text(body).casefold() != entry_text.strip().casefold():
            continue
        parsed = parse_typed_entry(body, default_scope=self.config.governance.default_scope)
        if parsed is None:
            continue
        return {
            "rel_path": path,
            "start_line": line_number,
            "entry_line": body,
            "scope": parsed.scope,
        }
    return None


def _apply_forget(self, *, rel_path: str, start_line: int, hard_delete: bool = False) -> None:
    """Invalidate or delete one canonical durable entry line."""
    path = self._resolve_writable_path(rel_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    line_index = start_line - 1
    if line_index < 0 or line_index >= len(lines):
        raise IndexError(f"{rel_path}:{start_line} is outside the file")
    if hard_delete:
        del lines[line_index]
    else:
        updated_line = self._invalidated_line(lines[line_index])
        if updated_line is None:
            raise ValueError(f"{rel_path}:{start_line} is not a typed durable entry")
        lines[line_index] = updated_line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _invalidated_line(self, current_line: str) -> str | None:
    """Return the invalidated form of a typed entry line."""
    match = re.match(r"^(?P<prefix>\s*[-*]\s+)(?P<body>.+?)\s*$", current_line)
    if match is None:
        return None
    body = match.group("body").strip()
    parsed = parse_typed_entry(body, default_scope=self.config.governance.default_scope)
    if parsed is None or parsed.item_type not in {"fact", "reflection", "opinion", "entity"}:
        return None
    entity = next(iter(parsed.entities), None) if parsed.item_type == "entity" else None
    updated = self._format_entry_line(
        kind=cast(Literal["fact", "reflection", "opinion", "entity"], parsed.item_type),
        text=self._typed_entry_text(body),
        entity=entity,
        confidence=parsed.confidence,
        scope=parsed.scope,
        fact_key=parsed.fact_key,
        importance=parsed.importance,
        tier=parsed.tier,
        access_count=parsed.access_count,
        last_access_date=parsed.last_access_date,
        injected_count=parsed.injected_count,
        confirmed_count=parsed.confirmed_count,
        bad_recall_count=parsed.bad_recall_count,
        invalidated_at=date.today().isoformat(),
        supersedes=parsed.supersedes,
        evidence=parsed.evidence,
        contradicts=parsed.contradicts,
    )
    return f"{match.group('prefix')}{updated}"


def _synced_line_from_row(self, current_line: str, *, row: sqlite3.Row) -> str | None:
    """Return a canonical line updated with SQLite lifecycle metadata."""
    match = re.match(r"^(?P<prefix>\s*[-*]\s+)(?P<body>.+?)\s*$", current_line)
    if match is None:
        return None
    body = match.group("body").strip()
    parsed = parse_typed_entry(body, default_scope=self.config.governance.default_scope)
    if parsed is None:
        return None
    if parsed.item_type not in {"fact", "reflection", "opinion", "entity"}:
        return None
    entity = next(iter(parsed.entities), None) if parsed.item_type == "entity" else None
    updated = self._format_entry_line(
        kind=cast(Literal["fact", "reflection", "opinion", "entity"], parsed.item_type),
        text=self._typed_entry_text(body),
        entity=entity,
        confidence=None if row["confidence"] is None else float(row["confidence"]),
        scope=str(row["scope"]),
        fact_key=None if row["fact_key"] is None else str(row["fact_key"]),
        importance=None if row["importance"] is None else float(row["importance"]),
        tier=self._normalize_tier(str(row["tier"])),
        access_count=int(row["access_count"] or 0),
        last_access_date=(
            None if row["last_access_date"] is None else str(row["last_access_date"])
        ),
        injected_count=int(row["injected_count"] or 0),
        confirmed_count=int(row["confirmed_count"] or 0),
        bad_recall_count=int(row["bad_recall_count"] or 0),
        invalidated_at=(None if row["invalidated_at"] is None else str(row["invalidated_at"])),
        supersedes=None if row["supersedes"] is None else str(row["supersedes"]),
        evidence=parsed.evidence,
        contradicts=parsed.contradicts,
    )
    return f"{match.group('prefix')}{updated}"


def _is_semantically_duplicate(
    self,
    *,
    kind: str,
    text: str,
    scope: str,
    threshold: float,
) -> tuple[bool, SearchHit | None]:
    """Return whether an entry already exists with near-identical semantics."""
    if not self.config.embedding.enabled:
        return False, None
    hits = self.search(
        text,
        max_results=1,
        lane="memory",
        scope=None if self.config.dedup.check_cross_scope else scope,
        auto_index=False,
    )
    if not hits:
        return False, None
    top_hit = hits[0]
    if top_hit.score >= threshold and top_hit.item_type == kind:
        return True, top_hit
    return False, None


def _increment_feedback_counts(
    self,
    *,
    item_ids: Sequence[int],
    column: str,
    date_column: str | None = None,
) -> dict[str, Any]:
    """Increment one lifecycle counter for typed durable memory rows."""
    normalized_ids = sorted({int(item_id) for item_id in item_ids if int(item_id) > 0})
    if not normalized_ids:
        return {"ok": True, "updated": 0}
    placeholders = ", ".join("?" for _ in normalized_ids)
    with self.connect() as conn:
        params: list[Any] = [date.today().isoformat()] if date_column is not None else []
        params.extend(normalized_ids)
        set_clause = f"{column} = {column} + 1"
        if date_column is not None:
            set_clause += f", {date_column} = ?"
        before = conn.total_changes
        conn.execute(
            f"""
                UPDATE search_items
                SET {set_clause}
                WHERE id IN ({placeholders})
                  AND lane = 'memory'
                  AND item_type IN ('fact', 'reflection', 'opinion', 'entity')
                """,
            params,
        )
        conn.commit()
        updated = conn.total_changes - before
    return {"ok": True, "updated": updated}


def _age_days(self, modified_at: str) -> float:
    """Return the age of one timestamp in days."""
    try:
        timestamp = datetime.fromisoformat(modified_at)
    except ValueError:
        return 0.0
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max((datetime.now(tz=UTC) - timestamp).total_seconds() / 86400.0, 0.0)


def _memory_pro_export_rows(self, conn: sqlite3.Connection, *, scope: str) -> Iterator[sqlite3.Row]:
    """Yield typed durable-memory rows for a single scope."""
    specs = (
        ("fact", "facts"),
        ("reflection", "reflections"),
        ("opinion", "opinions"),
        ("entity", "entities"),
    )
    for item_type, table_name in specs:
        yield from conn.execute(
            f"""
                SELECT
                    ? AS item_type,
                    search_items.rel_path,
                    search_items.start_line,
                    search_items.end_line,
                    search_items.modified_at,
                    search_items.confidence,
                    search_items.entities_json,
                    search_items.evidence_json,
                    {table_name}.text
                FROM {table_name}
                JOIN search_items ON search_items.id = {table_name}.item_id
                WHERE search_items.scope = ?
                  AND search_items.invalidated_at IS NULL
                ORDER BY search_items.rel_path, search_items.start_line
                """,
            (item_type, scope),
        )


def _allows_memory_pro_export_path(self, *, rel_path: str, include_daily: bool) -> bool:
    """Return whether *rel_path* is safe to export into the new backend."""
    if rel_path in self.config.memory_file_names:
        return True
    bank_prefix = f"{self.config.bank_dir}/"
    if rel_path.startswith(bank_prefix):
        return True
    if include_daily:
        daily_prefix = f"{self.config.daily_dir}/"
        return rel_path.startswith(daily_prefix)
    return False


def _load_entities_json(self, raw_value: Any) -> list[str]:
    """Decode a JSON list of entity names."""
    if not isinstance(raw_value, str):
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if isinstance(item, str)]


def _load_evidence_json(self, raw_value: Any) -> list[dict[str, Any]]:
    """Decode persisted evidence JSON into normalized provenance entries."""
    if not isinstance(raw_value, str):
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    evidence_entries: list[dict[str, Any]] = []
    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            continue
        try:
            evidence_entries.append(EvidenceEntry.from_dict(raw_entry).to_dict())
        except ValueError:
            continue
    return evidence_entries


def _memory_pro_importance(self, *, item_type: str, confidence: float | None) -> float:
    """Return a conservative import importance for the new memory backend."""
    if item_type == "opinion" and confidence is not None:
        return max(0.0, min(1.0, confidence))
    return MEMORY_PRO_IMPORTANCE_MAP[item_type]


def _memory_pro_timestamp_ms(self, value: str) -> int:
    """Convert a stored ISO timestamp into Unix milliseconds."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.now(tz=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _resolve_read_path(self, rel_path: str) -> pathlib.Path:
    """Resolve a safe readable path within the workspace."""
    path = (self.config.workspace_root / rel_path).resolve()
    resolve_under_workspace(self.config.workspace_root, path)
    if not path.exists():
        return path
    if path.is_dir():
        raise IsADirectoryError(path)
    return path


def _resolve_writable_path(self, rel_path: str) -> pathlib.Path:
    """Resolve a safe writable path within the workspace."""
    normalized = rel_path.strip()
    if not normalized.startswith(WRITABLE_PREFIXES):
        raise PermissionError(f"{rel_path} is not writable")
    path = (self.config.workspace_root / normalized).resolve()
    resolve_under_workspace(self.config.workspace_root, path)
    return path


def _store_target(
    self,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    entity: str | None = None,
) -> pathlib.Path:
    """Return the canonical target path for a durable entry."""
    bank_dir = self.config.workspace_root / self.config.bank_dir
    if kind == "fact":
        return bank_dir / "world.md"
    if kind == "reflection":
        return bank_dir / "experience.md"
    if kind == "opinion":
        return bank_dir / "opinions.md"
    name = slugify(entity or "general")
    return bank_dir / "entities" / f"{name}.md"


def _format_entry_line(
    self,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    text: str,
    entity: str | None = None,
    confidence: float | None = None,
    scope: str,
    fact_key: str | None = None,
    importance: float | None = None,
    tier: Tier = "working",
    access_count: int = 0,
    last_access_date: str | None = None,
    injected_count: int = 0,
    confirmed_count: int = 0,
    bad_recall_count: int = 0,
    invalidated_at: str | None = None,
    supersedes: str | None = None,
    evidence: Sequence[str] = (),
    contradicts: Sequence[str] = (),
) -> str:
    """Format a canonical typed entry line."""
    label = kind.capitalize()
    metadata: list[str] = []
    if kind == "entity":
        metadata.append((entity or text).strip())
    if kind == "opinion" and confidence is not None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        metadata.append(f"c={confidence:.2f}")
    metadata.append(f"scope={scope}")
    if importance is not None:
        metadata.append(f"importance={max(0.0, min(importance, 1.0)):.2f}")
    if tier != "working":
        metadata.append(f"tier={tier}")
    if access_count > 0:
        metadata.append(f"accessed={access_count}")
    if last_access_date:
        metadata.append(f"last_access={last_access_date}")
    if injected_count > 0:
        metadata.append(f"injected={injected_count}")
    if confirmed_count > 0:
        metadata.append(f"confirmed={confirmed_count}")
    if bad_recall_count > 0:
        metadata.append(f"bad_recall={bad_recall_count}")
    if fact_key:
        metadata.append(f"fact_key={fact_key}")
    if invalidated_at:
        metadata.append(f"invalidated={invalidated_at}")
    if supersedes:
        metadata.append(f"supersedes={supersedes}")
    if evidence:
        metadata.append(
            f"evidence={'|'.join(entry.strip() for entry in evidence if entry.strip())}"
        )
    if contradicts:
        metadata.append(
            f"contradicts={'|'.join(entry.strip() for entry in contradicts if entry.strip())}"
        )
    return f"{label}[{','.join(metadata)}]: {text.strip()}"


def _append_unique_entry(self, path: pathlib.Path, *, kind: str, entry_line: str) -> bool:
    """Append *entry_line* if it is not already present semantically."""
    ensure_parent(path)
    current = (
        path.read_text(encoding="utf-8") if path.exists() else self._document_header(path, kind)
    )
    lines = current.splitlines()
    target_identity = self._entry_identity(entry_line)
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped == entry_line.strip():
            return False
        if not stripped.startswith(("- ", "* ")):
            continue
        existing_entry = stripped[2:].strip()
        parsed = parse_typed_entry(
            existing_entry,
            default_scope=self.config.governance.default_scope,
        )
        if parsed is not None and parsed.invalidated_at is not None:
            continue
        existing_identity = self._entry_identity(existing_entry)
        if target_identity and existing_identity == target_identity:
            return False
    if current and not current.endswith("\n"):
        current += "\n"
    current += f"- {entry_line}\n"
    path.write_text(current, encoding="utf-8")
    return True


def _document_header(self, path: pathlib.Path, kind: str) -> str:
    """Return the default header for a writable canonical file."""
    if kind == "proposal":
        return "# Memory Proposals\n\n## Entries\n"
    if path.name in {"world.md", "experience.md", "opinions.md"}:
        mapped_kind = path.stem.rstrip("s") if path.stem != "experience" else "reflection"
        return BANK_HEADERS.get(mapped_kind, "# Entries\n\n")
    if path.parent.name == "entities":
        title = path.stem.replace("-", " ").title()
        return f"# Entity: {title}\n\n## Entries\n"
    return "# Entries\n\n"


def _entry_identity(self, entry_line: str) -> tuple[str, str, str | None] | None:
    """Return a semantic identity for a canonical entry line."""
    parsed = parse_typed_entry(
        entry_line.strip(), default_scope=self.config.governance.default_scope
    )
    if parsed is None:
        return None
    if parsed.fact_key:
        return (parsed.item_type, f"fact_key:{parsed.fact_key}", None)
    text = parsed.entry_line
    if ": " in text:
        _, normalized_body = text.split(": ", 1)
    else:
        normalized_body = text
    entity_name = next(iter(parsed.entities), None)
    return (parsed.item_type, normalized_body.lower(), entity_name)


def _build_proposal(
    self,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    entry_line: str,
    scope: str,
    source_rel_path: str,
    source_line: int,
    entity: str | None,
    confidence: float | None,
    mode: ReflectionMode,
) -> ProposalRecord:
    """Build a stable proposal record."""
    normalized_scope = validate_scope(scope)
    proposal_id = sha256(f"{source_rel_path}:{source_line}:{entry_line}")
    scope_auto_apply = should_auto_apply(normalized_scope, self.config.governance)
    auto_apply = mode == "safe" and scope_auto_apply
    if mode == "apply":
        auto_apply = scope_auto_apply
    status: Literal["pending", "applied"] = "applied" if auto_apply else "pending"
    return ProposalRecord(
        proposal_id=proposal_id,
        kind=kind,
        entry_line=entry_line,
        scope=normalized_scope,
        source_rel_path=source_rel_path,
        source_line=source_line,
        status=status,
        entity=entity,
        confidence=confidence,
    )


def _format_proposal_line(self, proposal: ProposalRecord) -> str:
    """Format a canonical proposal log entry."""
    metadata = [
        f"id={proposal.proposal_id}",
        f"status={proposal.status}",
        f"kind={proposal.kind}",
        f"scope={proposal.scope}",
        f"source={proposal.source_rel_path}#L{proposal.source_line}",
    ]
    if proposal.entity:
        metadata.append(f"entity={proposal.entity}")
    if proposal.confidence is not None:
        metadata.append(f"c={proposal.confidence:.2f}")
    return f"Proposal[{','.join(metadata)}]: {proposal.entry_line}"


def _proposal_kind(self, entry_line: str) -> Literal["fact", "reflection", "opinion", "entity"]:
    """Extract the proposal target kind from a proposal line."""
    parsed = parse_typed_entry(entry_line, default_scope=self.config.governance.default_scope)
    if parsed is None:
        return "fact"
    text = parsed.entry_line
    target = parse_typed_entry(
        text.split(": ", 1)[1], default_scope=self.config.governance.default_scope
    )
    if target is None:
        return "fact"
    if target.item_type in {"fact", "reflection", "opinion", "entity"}:
        return cast(Literal["fact", "reflection", "opinion", "entity"], target.item_type)
    return "fact"
