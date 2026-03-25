"""Canonical storage service for StrongClaw hypermemory.

This service owns canonical Markdown mutation and lifecycle maintenance.

Implementation started as a thin wrapper around `_engine/storage.py`, then was
incrementally internalized to make composition boundaries explicit and to
shrink `HypermemoryEngine`.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

from clawops.hypermemory._engine import storage as storage_impl
from clawops.hypermemory.capture import (
    CaptureCandidate,
    extract_candidates_llm,
    extract_candidates_regex,
    resolve_capture_api_key,
)
from clawops.hypermemory.config import HypermemoryConfig, resolve_under_workspace
from clawops.hypermemory.defaults import MEMORY_PRO_CATEGORY_MAP, WRITABLE_PREFIXES
from clawops.hypermemory.governance import ensure_writable_scope, validate_scope
from clawops.hypermemory.lifecycle import TierManager, compute_decay_score
from clawops.hypermemory.models import (
    ReflectionMode,
    ReflectionSummary,
    ReindexSummary,
    SearchHit,
    Tier,
)
from clawops.hypermemory.parser import iter_retained_notes
from clawops.hypermemory.utils import sha256
from clawops.observability import emit_structured_log


class CanonicalStoreService:
    """Service wrapper for canonical mutation and lifecycle operations."""

    def __init__(
        self,
        *,
        config: HypermemoryConfig,
        connect: Callable[[], sqlite3.Connection],
        is_dirty: Callable[[], bool],
        reindex: Callable[..., ReindexSummary],
        search: Callable[..., list[SearchHit]],
        get_fact: Callable[..., SearchHit | None],
    ) -> None:
        self.config: HypermemoryConfig = config
        self._connect = connect
        self._is_dirty = is_dirty
        self._reindex = reindex
        self._search = search
        self._get_fact = get_fact

    # ---- engine-facing callables expected by storage_impl ----

    def connect(self) -> sqlite3.Connection:
        return self._connect()

    def is_dirty(self) -> bool:
        return self._is_dirty()

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        return self._reindex(flush_metadata=flush_metadata)

    def search(self, query: str, **kwargs: Any) -> list[SearchHit]:
        return self._search(query, **kwargs)

    def get_fact(self, fact_key: str, **kwargs: Any) -> SearchHit | None:
        return self._get_fact(fact_key, **kwargs)

    # ---- public API surface delegated from HypermemoryEngine ----

    def export_memory_pro_import(
        self,
        *,
        scope: str | None = None,
        include_daily: bool = False,
        auto_index: bool = True,
    ) -> dict[str, Any]:
        resolved_scope = validate_scope(scope or self.config.governance.default_scope)
        if auto_index and self.is_dirty():
            self.reindex()
        memories: list[dict[str, Any]] = []
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
                metadata: dict[str, Any] = {
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
        return {
            "provider": "strongclaw-hypermemory",
            "scope": resolved_scope,
            "includeDaily": include_daily,
            "memories": memories,
        }

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
        mode: Literal["llm", "regex", "both"] | None = None,
    ) -> dict[str, Any]:
        resolved_mode = cast(Literal["llm", "regex", "both"], mode or self.config.capture.mode)
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

    def flush_metadata(self) -> dict[str, Any]:
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

    # ---- internal/test seam bindings reused by storage_impl ----

    _is_noise = storage_impl._is_noise
    _passes_admission = storage_impl._passes_admission
    _normalize_tier = storage_impl._normalize_tier
    _infer_fact_key = storage_impl._infer_fact_key
    _infer_query_fact_key = storage_impl._infer_query_fact_key
    _fact_category = storage_impl._fact_category
    _entry_hash_prefix = storage_impl._entry_hash_prefix
    _search_hit_text = storage_impl._search_hit_text
    _typed_entry_text = storage_impl._typed_entry_text
    _resolve_entry_reference = storage_impl._resolve_entry_reference
    _entry_reference_from_item_id = storage_impl._entry_reference_from_item_id
    _entry_reference_from_text = storage_impl._entry_reference_from_text
    _apply_forget = storage_impl._apply_forget
    _invalidated_line = storage_impl._invalidated_line
    _synced_line_from_row = storage_impl._synced_line_from_row
    _is_semantically_duplicate = storage_impl._is_semantically_duplicate
    _increment_feedback_counts = storage_impl._increment_feedback_counts
    _age_days = storage_impl._age_days
    _memory_pro_export_rows = storage_impl._memory_pro_export_rows
    _allows_memory_pro_export_path = storage_impl._allows_memory_pro_export_path
    _load_entities_json = storage_impl._load_entities_json
    _load_evidence_json = storage_impl._load_evidence_json
    _memory_pro_importance = storage_impl._memory_pro_importance
    _memory_pro_timestamp_ms = storage_impl._memory_pro_timestamp_ms
    _resolve_read_path = storage_impl._resolve_read_path
    _resolve_writable_path = storage_impl._resolve_writable_path
    _store_target = storage_impl._store_target
    _format_entry_line = storage_impl._format_entry_line
    _append_unique_entry = storage_impl._append_unique_entry
    _document_header = storage_impl._document_header
    _entry_identity = storage_impl._entry_identity
    _build_proposal = storage_impl._build_proposal
    _format_proposal_line = storage_impl._format_proposal_line
    _proposal_kind = storage_impl._proposal_kind
