"""Core engine for StrongClaw hypermemory."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from typing import Any, Literal

from clawops.common import ensure_parent
from clawops.hypermemory._engine.indexing import (
    _clear_derived_rows,
    _evidence_entries,
    _insert_typed_row,
    _iter_corpus_documents,
    _iter_documents,
    _iter_memory_documents,
    _missing_corpus_paths,
    _missing_required_corpus_paths,
    _rebuild_fact_registry,
)
from clawops.hypermemory._engine.indexing import reindex as _reindex
from clawops.hypermemory._engine.query import (
    _exact_fact_lookup,
    _filter_current_fact_hits,
    _search_invalidated_hits,
)
from clawops.hypermemory._engine.query import is_dirty as _is_dirty
from clawops.hypermemory._engine.query import read as _read
from clawops.hypermemory._engine.query import search as _search
from clawops.hypermemory._engine.query import status as _status
from clawops.hypermemory._engine.storage import (
    _age_days,
    _allows_memory_pro_export_path,
    _append_unique_entry,
    _apply_forget,
    _build_proposal,
    _document_header,
    _entry_hash_prefix,
    _entry_identity,
    _entry_reference_from_item_id,
    _entry_reference_from_text,
    _fact_category,
    _format_entry_line,
    _format_proposal_line,
    _increment_feedback_counts,
    _infer_fact_key,
    _infer_query_fact_key,
    _invalidated_line,
    _is_noise,
    _is_semantically_duplicate,
    _load_entities_json,
    _load_evidence_json,
    _memory_pro_export_rows,
    _memory_pro_importance,
    _memory_pro_timestamp_ms,
    _normalize_tier,
    _passes_admission,
    _proposal_kind,
    _resolve_entry_reference,
    _resolve_read_path,
    _resolve_writable_path,
    _search_hit_text,
    _store_target,
    _synced_line_from_row,
    _typed_entry_text,
)
from clawops.hypermemory._engine.verify import (
    _collection_has_hypermemory_vector_lanes,
    _hypermemory_probe_query,
    _observed_rerank_scorer,
    _rerank_probe_documents,
    _rerank_resolved_device,
    _verify_rerank_provider,
)
from clawops.hypermemory._engine.verify import verify as _verify
from clawops.hypermemory.config import HypermemoryConfig
from clawops.hypermemory.models import (
    DenseSearchCandidate,
    FusionMode,
    IndexedDocument,
    ReflectionMode,
    ReindexSummary,
    SearchBackend,
    SearchHit,
    SearchMode,
    SparseSearchCandidate,
    Tier,
)
from clawops.hypermemory.providers import (
    EmbeddingProvider,
    RerankProvider,
    create_embedding_provider,
    create_rerank_provider,
)
from clawops.hypermemory.qdrant_backend import QdrantBackend, VectorBackend
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.search_hit_mapper import row_to_search_hit
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.canonical_store_service import CanonicalStoreService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.sparse import SparseEncoder
from clawops.hypermemory.utils import (
    normalize_text,
    normalized_retrieval_text,
    point_id,
    sha256,
    slugify,
)


class HypermemoryEngine:
    """Markdown-canonical memory engine with a derived SQLite index."""

    def __init__(
        self,
        config: HypermemoryConfig,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        rerank_provider: RerankProvider | None = None,
        vector_backend: VectorBackend | None = None,
    ) -> None:
        self.config: HypermemoryConfig = config
        self._embedding_provider: EmbeddingProvider = (
            embedding_provider
            if embedding_provider is not None
            else create_embedding_provider(config.embedding)
        )
        self._rerank_provider: RerankProvider = (
            rerank_provider
            if rerank_provider is not None
            else create_rerank_provider(config.rerank)
        )
        self._qdrant_backend: VectorBackend = (
            vector_backend if vector_backend is not None else QdrantBackend(config.qdrant)
        )
        self.index = IndexService(connect=self.connect)
        self.backend = BackendService(
            config=self.config,
            connect=self.connect,
            embedding_provider=self._embedding_provider,
            vector_backend=self._qdrant_backend,
            index=self.index,
        )
        self.canonical_store = CanonicalStoreService(
            config=self.config,
            deps=self,
        )

    # ---- phase-1 composition: internal helper compatibility layer ----
    # `_engine/*` modules still call `self._...` today. We keep those names on the
    # engine, but delegate selected responsibilities into stateful services.

    def _backend_uses_qdrant(self) -> bool:
        return self.backend.backend_uses_qdrant()

    def _backend_uses_sparse_vectors(self) -> bool:
        return self.backend.backend_uses_sparse_vectors()

    def _canonical_backend(self, backend: SearchBackend) -> SearchBackend:
        return self.backend.canonical_backend(backend)

    def _backend_fingerprint(self) -> str:
        return self.backend.backend_fingerprint()

    def _backend_state_value(self, conn: sqlite3.Connection, key: str) -> str | None:
        return self.index.backend_state_value(conn, key)

    def _write_backend_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        self.index.write_backend_state(conn, key, value)

    def _count_rows(self, conn: sqlite3.Connection, table_name: str) -> int:
        return self.index.count_rows(conn, table_name)

    def _count_sparse_vector_items(self, conn: sqlite3.Connection) -> int:
        return self.index.count_sparse_vector_items(conn)

    def _write_sparse_state(self, conn: sqlite3.Connection, sparse_encoder: SparseEncoder) -> None:
        self.index.write_sparse_state(
            conn,
            sparse_encoder,
            enabled=self._backend_uses_sparse_vectors(),
        )

    def _load_sparse_encoder(self, conn: sqlite3.Connection) -> SparseEncoder | None:
        return self.index.load_sparse_encoder(
            conn,
            enabled=self._backend_uses_sparse_vectors(),
        )

    def _embedding_batches(
        self,
        vector_rows: list[dict[str, Any]],
    ) -> Iterator[list[dict[str, Any]]]:
        return self.backend.embedding_batches(vector_rows)

    def _embed_texts(self, texts: Sequence[str], *, purpose: str) -> list[list[float]]:
        return self.backend.embed_texts(texts, purpose=purpose)

    def _dense_search(
        self,
        *,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[DenseSearchCandidate], float]:
        return self.backend.dense_search(
            query=query,
            lane=lane,
            scope=scope,
            candidate_limit=candidate_limit,
        )

    def _sparse_search(
        self,
        *,
        conn: sqlite3.Connection,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[SparseSearchCandidate], float]:
        return self.backend.sparse_search(
            conn=conn,
            query=query,
            lane=lane,
            scope=scope,
            candidate_limit=candidate_limit,
        )

    def _sync_dense_backend(
        self,
        *,
        conn: sqlite3.Connection,
        vector_rows: list[dict[str, Any]],
        stale_point_ids: set[str],
        sparse_encoder: SparseEncoder,
    ) -> None:
        self.backend.sync_vectors(
            conn=conn,
            vector_rows=vector_rows,
            stale_point_ids=stale_point_ids,
            sparse_encoder=sparse_encoder,
        )

    def _vector_rows_for_documents(
        self,
        documents: Sequence[IndexedDocument],
    ) -> list[dict[str, str]]:
        return self.backend.vector_rows_for_documents(documents)

    def _sparse_encoder_for_documents(self, documents: Sequence[IndexedDocument]) -> SparseEncoder:
        return self.backend.sparse_encoder_for_documents(documents)

    def _sparse_fingerprint_for_documents(self, documents: Sequence[IndexedDocument]) -> str:
        return self.backend.sparse_fingerprint_for_documents(documents)

    def _current_sparse_fingerprint(self) -> str:
        return self.backend.sparse_fingerprint_for_documents(list(self._iter_documents()))

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        ensure_parent(self.config.db_path)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        return conn

    # Public API wrappers. These delegate to implementation functions in `_engine/*`.

    def status(self) -> dict[str, Any]:
        """Return index and governance status."""
        return _status(self)

    def is_dirty(self) -> bool:
        """Return whether the derived index differs from canonical Markdown files."""
        return _is_dirty(self)

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
        return _reindex(self, flush_metadata=flush_metadata)

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
        """Search the derived store through the dual-lane retrieval planner."""
        return _search(
            self,
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

    _observed_rerank_scorer = _observed_rerank_scorer
    _rerank_resolved_device = _rerank_resolved_device
    _rerank_probe_documents = _rerank_probe_documents
    _verify_rerank_provider = _verify_rerank_provider

    def verify(self) -> dict[str, Any]:
        """Verify the supported sparse+dense backend contract for hypermemory."""
        return _verify(self)

    def read(
        self,
        rel_path: str,
        *,
        from_line: int | None = None,
        lines: int | None = None,
    ) -> dict[str, Any]:
        """Read a canonical file returned by the memory index."""
        return _read(self, rel_path, from_line=from_line, lines=lines)

    def export_memory_pro_import(
        self,
        *,
        scope: str | None = None,
        include_daily: bool = False,
        auto_index: bool = True,
    ) -> dict[str, Any]:
        """Export durable hypermemory entries as `memory-lancedb-pro` import JSON."""
        return self.canonical_store.export_memory_pro_import(
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
        return self.canonical_store.store(
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
        return self.canonical_store.update(
            rel_path=rel_path,
            find_text=find_text,
            replace_text=replace_text,
            replace_all=replace_all,
        )

    def reflect(self, *, mode: ReflectionMode = "safe") -> dict[str, Any]:
        """Promote retained daily-log entries into durable bank pages via proposals."""
        return self.canonical_store.reflect(mode=mode)

    def capture(
        self,
        *,
        messages: Sequence[tuple[int, str, str]],
        mode: Literal["llm", "regex", "both"] | None = None,
    ) -> dict[str, Any]:
        """Extract and store durable memory candidates from conversation messages."""
        return self.canonical_store.capture(messages=messages, mode=mode)

    def forget(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        entry_text: str | None = None,
        hard_delete: bool = False,
    ) -> dict[str, Any]:
        """Invalidate or delete a durable memory entry."""
        return self.canonical_store.forget(
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
        return self.canonical_store.supersede(
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
        return self.canonical_store.record_access(item_ids=item_ids)

    def record_injection(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that items were auto-injected into a prompt."""
        return self.canonical_store.record_injection(item_ids=item_ids)

    def record_confirmation(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that recalled items were confirmed useful."""
        return self.canonical_store.record_confirmation(item_ids=item_ids)

    def record_bad_recall(self, *, item_ids: Sequence[int]) -> dict[str, Any]:
        """Record that recalled items were contradicted or unhelpful."""
        return self.canonical_store.record_bad_recall(item_ids=item_ids)

    def flush_metadata(self) -> dict[str, Any]:
        """Flush lifecycle metadata from SQLite rows back into canonical Markdown."""
        return self.canonical_store.flush_metadata()

    def run_lifecycle(self) -> dict[str, Any]:
        """Evaluate lifecycle scores and promote or demote tiers."""
        return self.canonical_store.run_lifecycle()

    def get_fact(
        self,
        fact_key: str,
        *,
        conn: sqlite3.Connection | None = None,
        scope: str | None = None,
    ) -> SearchHit | None:
        """Return the current active value for a canonical fact slot."""
        return self.canonical_store.get_fact(fact_key, conn=conn, scope=scope)

    def list_facts(
        self,
        *,
        category: str | None = None,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """List current canonical facts from the registry."""
        return self.canonical_store.list_facts(category=category, scope=scope)

    def benchmark_cases(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        """Run simple benchmark cases against the current engine."""
        return self.canonical_store.benchmark_cases(cases)

    # Pure helper wrappers. `_engine/*` no longer depends on these being bound on the
    # engine, but we keep them for internal/test seams.

    def _normalized_retrieval_text(self, title: str, snippet: str) -> str:
        return normalized_retrieval_text(title, snippet)

    def _normalize_text(self, text: str) -> tuple[str, ...]:
        return normalize_text(text)

    def _point_id(
        self,
        *,
        document_rel_path: str,
        item_type: str,
        start_line: int,
        end_line: int,
        snippet: str,
    ) -> str:
        return point_id(
            document_rel_path=document_rel_path,
            item_type=item_type,
            start_line=start_line,
            end_line=end_line,
            snippet=snippet,
        )

    def _slugify(self, value: str) -> str:
        return slugify(value)

    def _sha256(self, value: str) -> str:
        return sha256(value)

    # Internal/test seam bindings. These are intentionally attached to simplify tests
    # and to avoid duplicating utility functions across modules.
    _is_noise = _is_noise
    _passes_admission = _passes_admission
    _normalize_tier = _normalize_tier
    _infer_fact_key = _infer_fact_key
    _infer_query_fact_key = _infer_query_fact_key
    _fact_category = _fact_category
    _entry_hash_prefix = _entry_hash_prefix
    _search_hit_text = _search_hit_text
    _typed_entry_text = _typed_entry_text
    _resolve_entry_reference = _resolve_entry_reference
    _entry_reference_from_item_id = _entry_reference_from_item_id
    _entry_reference_from_text = _entry_reference_from_text
    _apply_forget = _apply_forget
    _invalidated_line = _invalidated_line
    _synced_line_from_row = _synced_line_from_row
    _row_to_search_hit = row_to_search_hit
    _rebuild_fact_registry = _rebuild_fact_registry
    _exact_fact_lookup = _exact_fact_lookup
    _filter_current_fact_hits = _filter_current_fact_hits
    _search_invalidated_hits = _search_invalidated_hits
    _is_semantically_duplicate = _is_semantically_duplicate
    _increment_feedback_counts = _increment_feedback_counts
    _age_days = _age_days
    _memory_pro_export_rows = _memory_pro_export_rows
    _allows_memory_pro_export_path = _allows_memory_pro_export_path
    _load_entities_json = _load_entities_json
    _load_evidence_json = _load_evidence_json
    _memory_pro_importance = _memory_pro_importance
    _memory_pro_timestamp_ms = _memory_pro_timestamp_ms
    _iter_documents = _iter_documents
    _iter_memory_documents = _iter_memory_documents
    _iter_corpus_documents = _iter_corpus_documents
    _missing_corpus_paths = _missing_corpus_paths
    _missing_required_corpus_paths = _missing_required_corpus_paths
    _clear_derived_rows = _clear_derived_rows
    _insert_typed_row = _insert_typed_row
    _evidence_entries = _evidence_entries
    _resolve_read_path = _resolve_read_path
    _resolve_writable_path = _resolve_writable_path
    _store_target = _store_target
    _format_entry_line = _format_entry_line
    _append_unique_entry = _append_unique_entry
    _document_header = _document_header
    _entry_identity = _entry_identity
    _build_proposal = _build_proposal
    _format_proposal_line = _format_proposal_line
    _proposal_kind = _proposal_kind
    # Vector backend / sparse state helpers are implemented as delegating methods.
    _collection_has_hypermemory_vector_lanes = _collection_has_hypermemory_vector_lanes
    _hypermemory_probe_query = _hypermemory_probe_query
    # End of HypermemoryEngine helper bindings.
