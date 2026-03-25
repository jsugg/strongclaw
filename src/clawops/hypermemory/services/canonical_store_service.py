"""Canonical storage service for StrongClaw hypermemory.

This service owns canonical Markdown mutation and lifecycle maintenance.

Today it delegates to the existing `_engine/storage.py` implementation functions.
The purpose of this wrapper is to keep `HypermemoryEngine` smaller and to make
composition boundaries explicit via constructor injection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from typing import Any, Literal

from clawops.hypermemory._engine import storage as storage_impl
from clawops.hypermemory.config import HypermemoryConfig
from clawops.hypermemory.models import (
    ReflectionMode,
    ReindexSummary,
    SearchHit,
    Tier,
)


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
        return storage_impl.export_memory_pro_import(
            self,
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
        return storage_impl.store(
            self,
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
        return storage_impl.update(
            self,
            rel_path=rel_path,
            find_text=find_text,
            replace_text=replace_text,
            replace_all=replace_all,
        )

    def reflect(self, *, mode: ReflectionMode = "safe") -> dict[str, Any]:
        return storage_impl.reflect(self, mode=mode)

    def capture(
        self,
        *,
        messages: Sequence[tuple[int, str, str]],
        mode: Literal["llm", "regex", "both"] | None = None,
    ) -> dict[str, Any]:
        return storage_impl.capture(self, messages=messages, mode=mode)

    def forget(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        entry_text: str | None = None,
        hard_delete: bool = False,
    ) -> dict[str, Any]:
        return storage_impl.forget(
            self,
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
        return storage_impl.supersede(
            self,
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

    def flush_metadata(self) -> dict[str, Any]:
        return storage_impl.flush_metadata(self)

    def run_lifecycle(self) -> dict[str, Any]:
        return storage_impl.run_lifecycle(self)

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
