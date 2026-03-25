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
from clawops.hypermemory.config import HypermemoryConfig, resolve_under_workspace
from clawops.hypermemory.governance import ensure_writable_scope
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
