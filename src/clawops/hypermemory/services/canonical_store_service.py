"""Canonical storage service for StrongClaw hypermemory."""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol, cast

from clawops.hypermemory.canonical_store_helpers import (
    allows_memory_pro_export_path,
    append_unique_entry,
    build_proposal,
    document_header,
    entry_hash_prefix,
    entry_identity,
    fact_category,
    format_entry_line,
    format_proposal_line,
    infer_fact_key,
    infer_query_fact_key,
    is_noise_entry,
    load_entities_json,
    load_evidence_json,
    memory_pro_importance,
    memory_pro_timestamp_ms,
    normalize_tier,
    passes_admission,
    proposal_kind,
    resolve_writable_path,
    search_hit_text,
    store_target,
    typed_entry_text,
)
from clawops.hypermemory.capture import (
    CaptureCandidate,
    extract_candidates_llm,
    extract_candidates_regex,
    resolve_capture_api_key,
)
from clawops.hypermemory.config import HypermemoryConfig, resolve_under_workspace
from clawops.hypermemory.contracts import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkResult,
    FlushMetadataResult,
    MemoryProImportResult,
    MemoryProRecord,
    MemoryProRecordMetadata,
)
from clawops.hypermemory.defaults import MEMORY_PRO_CATEGORY_MAP, WRITABLE_PREFIXES
from clawops.hypermemory.governance import ensure_writable_scope, validate_scope
from clawops.hypermemory.lifecycle import TierManager, compute_decay_score
from clawops.hypermemory.models import (
    CaptureMode,
    FactCategory,
    FusionMode,
    ProposalRecord,
    ReflectionMode,
    ReflectionSummary,
    ReindexSummary,
    SearchBackend,
    SearchHit,
    SearchMode,
    Tier,
)
from clawops.hypermemory.parser import iter_retained_notes, parse_typed_entry
from clawops.hypermemory.search_hit_mapper import row_to_search_hit as row_to_search_hit_impl
from clawops.hypermemory.utils import sha256
from clawops.observability import emit_structured_log


class CanonicalStoreDeps(Protocol):
    """Engine-like dependency surface required by CanonicalStoreService.

    We keep this structural and minimal to avoid import cycles while still
    providing strong typing for the canonical store.
    """

    def connect(self) -> sqlite3.Connection: ...

    def is_dirty(self) -> bool: ...

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary: ...

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        min_score: float | None = None,
        lane: SearchMode = "all",
        scope: str | None = None,
        auto_index: bool = True,
        include_explain: bool = False,
        backend: SearchBackend | None = None,
        dense_candidate_pool: int | None = None,
        sparse_candidate_pool: int | None = None,
        fusion: FusionMode | None = None,
        include_invalidated: bool = False,
    ) -> list[SearchHit]: ...


class CanonicalStoreService:
    """Service wrapper for canonical mutation and lifecycle operations."""

    def __init__(
        self,
        *,
        config: HypermemoryConfig,
        deps: CanonicalStoreDeps,
    ) -> None:
        self.config: HypermemoryConfig = config
        self._deps = deps

    # ---- engine-facing callables consumed by the public canonical store ----

    def connect(self) -> sqlite3.Connection:
        return self._deps.connect()

    def is_dirty(self) -> bool:
        return self._deps.is_dirty()

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        return self._deps.reindex(flush_metadata=flush_metadata)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        min_score: float | None = None,
        lane: SearchMode = "all",
        scope: str | None = None,
        auto_index: bool = True,
        include_explain: bool = False,
        backend: SearchBackend | None = None,
        dense_candidate_pool: int | None = None,
        sparse_candidate_pool: int | None = None,
        fusion: FusionMode | None = None,
        include_invalidated: bool = False,
    ) -> list[SearchHit]:
        return self._deps.search(
            query,
            max_results=max_results,
            min_score=min_score,
            lane=lane,
            scope=scope,
            auto_index=auto_index,
            include_explain=include_explain,
            backend=backend,
            dense_candidate_pool=dense_candidate_pool,
            sparse_candidate_pool=sparse_candidate_pool,
            fusion=fusion,
            include_invalidated=include_invalidated,
        )

    # ---- public API surface delegated from HypermemoryEngine ----

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

    def benchmark_cases(self, cases: list[BenchmarkCase]) -> BenchmarkResult:
        """Run simple benchmark cases against the current engine."""
        results: list[BenchmarkCaseResult] = []
        passed = 0
        for case in cases:
            name = case["name"]
            query = case["query"]
            expected_paths = set(case.get("expectedPaths", []))
            hits = self.search(
                query,
                max_results=case.get("maxResults", self.config.default_max_results),
                lane=case.get("lane", "all"),
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
        payload: BenchmarkResult = {
            "provider": "strongclaw-hypermemory",
            "cases": results,
            "passed": passed,
            "total": len(results),
        }
        return payload

    def export_memory_pro_import(
        self,
        *,
        scope: str | None = None,
        include_daily: bool = False,
        auto_index: bool = True,
    ) -> MemoryProImportResult:
        resolved_scope = validate_scope(scope or self.config.governance.default_scope)
        if auto_index and self.is_dirty():
            self.reindex()
        memories: list[MemoryProRecord] = []
        with self.connect() as conn:
            for row in self._memory_pro_export_rows(conn, scope=resolved_scope):
                rel_path = str(row["rel_path"])
                if not self._allows_memory_pro_export_path(
                    rel_path=rel_path, include_daily=include_daily
                ):
                    continue
                item_type = str(row["item_type"])
                text = str(row["text"]).strip()
                if not text:
                    continue
                start_line = int(row["start_line"])
                end_line = int(row["end_line"])
                confidence = None if row["confidence"] is None else float(row["confidence"])
                entities = self._load_entities_json(row["entities_json"])
                evidence = self._load_evidence_json(row["evidence_json"])
                source_fingerprint = (
                    f"{item_type}:{resolved_scope}:{rel_path}:{start_line}:{end_line}:{text}"
                )
                metadata: MemoryProRecordMetadata = {
                    "source": "strongclaw-hypermemory",
                    "hypermemory": {
                        "itemType": item_type,
                        "scope": resolved_scope,
                        "sourcePath": rel_path,
                        "startLine": start_line,
                        "endLine": end_line,
                        "entities": entities,
                        "evidence": evidence,
                    },
                }
                if confidence is not None:
                    metadata["hypermemory"]["confidence"] = confidence
                memories.append(
                    {
                        "id": f"strongclaw-hypermemory:{sha256(source_fingerprint)}",
                        "text": text,
                        "category": MEMORY_PRO_CATEGORY_MAP[item_type],
                        "importance": self._memory_pro_importance(
                            item_type=item_type, confidence=confidence
                        ),
                        "timestamp": self._memory_pro_timestamp_ms(str(row["modified_at"])),
                        "metadata": metadata,
                    }
                )
        payload: MemoryProImportResult = {
            "provider": "strongclaw-hypermemory",
            "scope": resolved_scope,
            "includeDaily": include_daily,
            "memories": memories,
        }
        return payload

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
        entry_text = text.strip()
        if not entry_text:
            raise ValueError("text must not be empty")

        resolved_scope = ensure_writable_scope(
            scope or self.config.governance.default_scope, self.config.governance
        )
        if self._is_noise(entry_text):
            return {
                "ok": True,
                "stored": False,
                "noise": True,
                "scope": resolved_scope,
            }

        normalized_fact_key = fact_key
        if not normalized_fact_key and self.config.fact_registry.auto_infer_keys:
            normalized_fact_key = self._infer_fact_key(kind=kind, text=entry_text)

        normalized_tier = self._normalize_tier(tier)
        if (
            not _skip_preindex_sync
            and (self.config.dedup.enabled or self.config.fact_registry.enabled)
            and self.is_dirty()
        ):
            self.reindex()

        current_fact_hit: SearchHit | None = None
        if (
            not _skip_dedup
            and normalized_fact_key
            and self.config.dedup.enabled
            and self.config.dedup.typed_slots_enabled
        ):
            with self.connect() as conn:
                current_fact_hit = self.get_fact(
                    normalized_fact_key,
                    conn=conn,
                    scope=None if self.config.dedup.check_cross_scope else resolved_scope,
                )
            if current_fact_hit is not None:
                current_text = self._search_hit_text(current_fact_hit)
                if (
                    current_fact_hit.item_type == kind
                    and current_text.casefold() == entry_text.casefold()
                ):
                    return {
                        "ok": True,
                        "stored": False,
                        "duplicate": True,
                        "scope": resolved_scope,
                        "match": current_fact_hit.to_dict(),
                    }
                return self.supersede(
                    item_id=current_fact_hit.item_id,
                    new_text=entry_text,
                    kind=kind,
                    entity=entity,
                    confidence=confidence,
                    scope=resolved_scope,
                    fact_key=normalized_fact_key,
                    importance=importance,
                    tier=normalized_tier,
                )

        if self.config.dedup.enabled and not _skip_dedup:
            is_duplicate, match = self._is_semantically_duplicate(
                kind=kind,
                text=entry_text,
                scope=resolved_scope,
                threshold=self.config.dedup.similarity_threshold,
            )
            if is_duplicate and match is not None:
                return {
                    "ok": True,
                    "stored": False,
                    "duplicate": True,
                    "scope": resolved_scope,
                    "match": match.to_dict(),
                }

        target = self._store_target(kind=kind, entity=entity)
        entry_line = self._format_entry_line(
            kind=kind,
            text=entry_text,
            entity=entity,
            confidence=confidence,
            scope=resolved_scope,
            fact_key=normalized_fact_key,
            importance=importance,
            tier=normalized_tier,
            supersedes=supersedes,
        )
        changed = self._append_unique_entry(target, kind=kind, entry_line=entry_line)
        summary = self.reindex(flush_metadata=not _skip_preflush_on_reindex)
        return {
            "ok": True,
            "stored": changed,
            "path": resolve_under_workspace(self.config.workspace_root, target),
            "entry": entry_line,
            "scope": resolved_scope,
            "factKey": normalized_fact_key,
            "index": summary.to_dict(),
        }

    def update(
        self,
        *,
        rel_path: str,
        find_text: str,
        replace_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        path = self._resolve_writable_path(rel_path)
        if not path.exists():
            raise FileNotFoundError(path)
        content = path.read_text(encoding="utf-8")
        replacements = content.count(find_text) if replace_all else int(find_text in content)
        if replacements == 0:
            return {"ok": True, "path": rel_path, "replacements": 0}
        updated = (
            content.replace(find_text, replace_text)
            if replace_all
            else content.replace(find_text, replace_text, 1)
        )
        path.write_text(updated, encoding="utf-8")
        summary = self.reindex(flush_metadata=False)
        return {
            "ok": True,
            "path": rel_path,
            "replacements": replacements,
            "index": summary.to_dict(),
        }

    def reflect(self, *, mode: ReflectionMode = "safe") -> dict[str, Any]:
        proposed = 0
        applied = 0
        pending = 0
        reflected: dict[str, int] = {"fact": 0, "reflection": 0, "opinion": 0, "entity": 0}
        proposals_path = self.config.proposals_path
        daily_dir = self.config.workspace_root / self.config.daily_dir
        for path in sorted(daily_dir.glob("*.md")):
            source_rel_path = resolve_under_workspace(self.config.workspace_root, path)
            for note in iter_retained_notes(
                path, default_scope=self.config.governance.default_scope
            ):
                note_kind = note.kind
                if note_kind not in reflected:
                    continue
                typed_note_kind = cast(
                    Literal["fact", "reflection", "opinion", "entity"],
                    note_kind,
                )
                proposal = self._build_proposal(
                    kind=typed_note_kind,
                    entry_line=note.entry_line,
                    scope=note.scope,
                    source_rel_path=source_rel_path,
                    source_line=note.source_line,
                    entity=note.entity,
                    confidence=note.confidence,
                    mode=mode,
                )
                if self._append_unique_entry(
                    proposals_path,
                    kind="proposal",
                    entry_line=self._format_proposal_line(proposal),
                ):
                    proposed += 1
                if proposal.status == "applied":
                    target = self._store_target(kind=proposal.kind, entity=proposal.entity)
                    if self._append_unique_entry(
                        target,
                        kind=proposal.kind,
                        entry_line=proposal.entry_line,
                    ):
                        reflected[proposal.kind] += 1
                        applied += 1
                else:
                    pending += 1
        summary = self.reindex()
        lifecycle_summary: dict[str, Any] | None = None
        if self.config.decay.enabled:
            lifecycle_summary = self.run_lifecycle()
        return ReflectionSummary(
            proposed=proposed,
            applied=applied,
            pending=pending,
            reflected=reflected,
            index=summary,
        ).to_dict() | ({"lifecycle": lifecycle_summary} if lifecycle_summary is not None else {})

    def capture(
        self,
        *,
        messages: Sequence[tuple[int, str, str]],
        mode: CaptureMode | None = None,
    ) -> dict[str, Any]:
        resolved_mode = mode or self.config.capture.mode
        candidates: list[CaptureCandidate] = []
        if (
            resolved_mode in {"llm", "both"}
            and self.config.capture.llm.endpoint
            and self.config.capture.llm.model
        ):
            try:
                candidates.extend(
                    extract_candidates_llm(
                        messages,
                        endpoint=self.config.capture.llm.endpoint,
                        model=self.config.capture.llm.model,
                        api_key=resolve_capture_api_key(
                            api_key_env=self.config.capture.llm.api_key_env,
                            api_key=self.config.capture.llm.api_key,
                        ),
                        timeout_ms=self.config.capture.llm.timeout_ms,
                        batch_size=self.config.capture.batch_size,
                        batch_overlap=self.config.capture.batch_overlap,
                    )
                )
            except Exception as err:
                emit_structured_log(
                    "clawops.hypermemory.capture.llm_error",
                    {"error": str(err)},
                )
                if resolved_mode == "llm":
                    candidates = []
        if not candidates or resolved_mode in {"regex", "both"}:
            regex_candidates = extract_candidates_regex(messages)
            existing_keys = {candidate.text.casefold() for candidate in candidates}
            for candidate in regex_candidates:
                if candidate.text.casefold() not in existing_keys:
                    candidates.append(candidate)
        captured = 0
        skipped_duplicate = 0
        skipped_noise = 0
        skipped_admission = 0
        for candidate in candidates[: self.config.capture.max_candidates_per_session]:
            if self._is_noise(candidate.text):
                skipped_noise += 1
                continue
            if not self._passes_admission(candidate):
                skipped_admission += 1
                continue
            result = self.store(
                kind=cast(
                    Literal["fact", "reflection", "opinion", "entity"],
                    candidate.kind,
                ),
                text=candidate.text,
                entity=candidate.entity,
                confidence=candidate.confidence,
                fact_key=candidate.fact_key,
                importance=candidate.confidence,
            )
            if result.get("duplicate"):
                skipped_duplicate += 1
            elif result.get("stored") or result.get("superseded"):
                captured += 1
        payload = {
            "ok": True,
            "candidates": len(candidates),
            "captured": captured,
            "skippedDuplicate": skipped_duplicate,
            "skippedNoise": skipped_noise,
            "skippedAdmission": skipped_admission,
        }
        emit_structured_log("clawops.hypermemory.capture", payload)
        return payload

    def forget(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        entry_text: str | None = None,
        hard_delete: bool = False,
    ) -> dict[str, Any]:
        target = self._resolve_entry_reference(query=query, path=path, entry_text=entry_text)
        if target is None:
            return {"ok": True, "forgotten": False}
        self._apply_forget(
            rel_path=target["rel_path"],
            start_line=target["start_line"],
            hard_delete=hard_delete,
        )
        summary = self.reindex(flush_metadata=False)
        return {
            "ok": True,
            "forgotten": True,
            "path": target["rel_path"],
            "startLine": target["start_line"],
            "hardDelete": hard_delete,
            "index": summary.to_dict(),
        }

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
        target = self._resolve_entry_reference(item_id=item_id, entry_text=old_entry_text)
        if target is None:
            raise FileNotFoundError("unable to resolve the superseded entry")
        old_hash = self._entry_hash_prefix(target["entry_line"])
        self._apply_forget(rel_path=target["rel_path"], start_line=target["start_line"])
        store_payload = self.store(
            kind=kind,
            text=new_text,
            entity=entity,
            confidence=confidence,
            scope=scope or target["scope"],
            fact_key=fact_key,
            importance=importance,
            tier=tier,
            supersedes=old_hash,
            _skip_preindex_sync=True,
            _skip_preflush_on_reindex=True,
            _skip_dedup=True,
        )
        store_payload["superseded"] = True
        store_payload["supersededEntry"] = {
            "path": target["rel_path"],
            "startLine": target["start_line"],
            "hash": old_hash,
        }
        return store_payload

    def record_access(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record retrieval access for durable typed memory items."""
        return self._increment_feedback_counts(
            item_ids=item_ids,
            column="access_count",
            date_column="last_access_date",
        )

    def record_injection(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that items were auto-injected into a prompt."""
        return self._increment_feedback_counts(item_ids=item_ids, column="injected_count")

    def record_confirmation(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that recalled items were confirmed useful."""
        return self._increment_feedback_counts(item_ids=item_ids, column="confirmed_count")

    def record_bad_recall(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that recalled items were contradicted or unhelpful."""
        return self._increment_feedback_counts(item_ids=item_ids, column="bad_recall_count")

    def flush_metadata(self) -> FlushMetadataResult:
        if not self.config.db_path.exists():
            return {"ok": True, "updatedFiles": 0, "updatedEntries": 0}
        updated_files = 0
        updated_entries = 0
        try:
            with self.connect() as conn:
                rows = conn.execute("""
                    SELECT
                        id,
                        rel_path,
                        start_line,
                        snippet,
                        item_type,
                        scope,
                        confidence,
                        importance,
                        tier,
                        access_count,
                        last_access_date,
                        injected_count,
                        confirmed_count,
                        bad_recall_count,
                        fact_key,
                        invalidated_at,
                        supersedes
                    FROM search_items
                    WHERE lane = 'memory'
                      AND item_type IN ('fact', 'reflection', 'opinion', 'entity')
                    ORDER BY rel_path, start_line DESC
                    """).fetchall()
                rows_by_path: dict[str, list[sqlite3.Row]] = defaultdict(list)
                for row in rows:
                    rel_path = str(row["rel_path"])
                    if not rel_path.startswith(WRITABLE_PREFIXES):
                        continue
                    rows_by_path[rel_path].append(row)
                for rel_path, path_rows in rows_by_path.items():
                    path = self._resolve_writable_path(rel_path)
                    if not path.exists():
                        continue
                    lines = path.read_text(encoding="utf-8").splitlines()
                    changed = False
                    for row in path_rows:
                        line_index = int(row["start_line"]) - 1
                        if line_index < 0 or line_index >= len(lines):
                            continue
                        updated_line = self._synced_line_from_row(lines[line_index], row=row)
                        if updated_line is None or updated_line == lines[line_index]:
                            continue
                        lines[line_index] = updated_line
                        changed = True
                        updated_entries += 1
                    if changed:
                        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        updated_files += 1
        except sqlite3.DatabaseError:
            return {"ok": True, "updatedFiles": 0, "updatedEntries": 0}
        return {"ok": True, "updatedFiles": updated_files, "updatedEntries": updated_entries}

    def run_lifecycle(self) -> dict[str, Any]:
        if not self.config.decay.enabled:
            return {"ok": True, "evaluated": 0, "changed": 0}
        manager = TierManager(self.config.decay)
        changed = 0
        evaluated = 0
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT id, modified_at, importance, tier, access_count
                FROM search_items
                WHERE lane = 'memory'
                  AND item_type IN ('fact', 'reflection', 'opinion', 'entity')
                  AND invalidated_at IS NULL
                """).fetchall()
            for row in rows:
                evaluated += 1
                current_tier = self._normalize_tier(str(row["tier"]))
                composite = compute_decay_score(
                    age_days=max(self._age_days(str(row["modified_at"])), 0.0),
                    access_count=int(row["access_count"] or 0),
                    importance=float(row["importance"] or 0.5),
                    tier=current_tier,
                    config=self.config.decay,
                )
                next_tier = manager.evaluate_tier(
                    current_tier=current_tier,
                    composite=composite,
                    access_count=int(row["access_count"] or 0),
                    importance=float(row["importance"] or 0.5),
                    age_days=self._age_days(str(row["modified_at"])),
                )
                if next_tier == current_tier:
                    continue
                conn.execute(
                    "UPDATE search_items SET tier = ? WHERE id = ?",
                    (next_tier, int(row["id"])),
                )
                changed += 1
            conn.commit()
        flush_payload = self.flush_metadata()
        return {"ok": True, "evaluated": evaluated, "changed": changed, "flush": flush_payload}

    def _is_noise(self, text: str) -> bool:
        return is_noise_entry(text, config=self.config)

    def _passes_admission(self, candidate: CaptureCandidate) -> bool:
        return passes_admission(candidate, config=self.config)

    def _normalize_tier(self, value: str | Tier | None) -> Tier:
        return normalize_tier(value)

    def _infer_fact_key(
        self,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        text: str,
    ) -> str | None:
        return infer_fact_key(kind=kind, text=text)

    def _infer_query_fact_key(self, query: str) -> str | None:
        return infer_query_fact_key(query)

    def _fact_category(self, fact_key: str) -> FactCategory:
        return fact_category(fact_key)

    def _entry_hash_prefix(self, entry_line: str) -> str:
        return entry_hash_prefix(entry_line)

    def _search_hit_text(self, hit: SearchHit) -> str:
        return search_hit_text(hit)

    def _typed_entry_text(self, entry_line: str) -> str:
        return typed_entry_text(entry_line)

    def _resolve_entry_reference(
        self,
        *,
        item_id: int | None = None,
        query: str | None = None,
        path: str | None = None,
        entry_text: str | None = None,
    ) -> dict[str, Any] | None:
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

    def _row_to_search_hit(self, row: sqlite3.Row) -> SearchHit:
        return row_to_search_hit_impl(row)

    def _is_semantically_duplicate(
        self,
        *,
        kind: str,
        text: str,
        scope: str,
        threshold: float,
    ) -> tuple[bool, SearchHit | None]:
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
        try:
            timestamp = datetime.fromisoformat(modified_at)
        except ValueError:
            return 0.0
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return max((datetime.now(tz=UTC) - timestamp).total_seconds() / 86400.0, 0.0)

    def _memory_pro_export_rows(
        self, conn: sqlite3.Connection, *, scope: str
    ) -> Iterator[sqlite3.Row]:
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
        return allows_memory_pro_export_path(
            self.config,
            rel_path=rel_path,
            include_daily=include_daily,
        )

    def _load_entities_json(self, raw_value: object) -> list[str]:
        return load_entities_json(raw_value)

    def _load_evidence_json(self, raw_value: object) -> list[dict[str, object]]:
        return load_evidence_json(raw_value)

    def _memory_pro_importance(self, *, item_type: str, confidence: float | None) -> float:
        return memory_pro_importance(item_type=item_type, confidence=confidence)

    def _memory_pro_timestamp_ms(self, value: str) -> int:
        return memory_pro_timestamp_ms(value)

    def _resolve_writable_path(self, rel_path: str) -> pathlib.Path:
        return resolve_writable_path(self.config, rel_path)

    def _store_target(
        self,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        entity: str | None = None,
    ) -> pathlib.Path:
        return store_target(self.config, kind=kind, entity=entity)

    def _format_entry_line(self, **kwargs: Any) -> str:
        return format_entry_line(**kwargs)

    def _append_unique_entry(self, path: pathlib.Path, *, kind: str, entry_line: str) -> bool:
        return append_unique_entry(self.config, path, kind=kind, entry_line=entry_line)

    def _document_header(self, path: pathlib.Path, kind: str) -> str:
        return document_header(path, kind=kind)

    def _entry_identity(self, entry_line: str) -> tuple[str, str, str | None] | None:
        return entry_identity(entry_line, config=self.config)

    def _build_proposal(self, **kwargs: Any) -> ProposalRecord:
        return build_proposal(self.config, **kwargs)

    def _format_proposal_line(self, proposal: ProposalRecord) -> str:
        return format_proposal_line(proposal)

    def _proposal_kind(self, entry_line: str) -> Literal["fact", "reflection", "opinion", "entity"]:
        return proposal_kind(entry_line, config=self.config)
