"""Core engine for StrongClaw hypermemory."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from clawops.common import ensure_parent
from clawops.hypermemory.config import HypermemoryConfig
from clawops.hypermemory.contracts import (
    BenchmarkResult,
    CorpusPathStatus,
    FlushMetadataResult,
    IndexingDeps,
    MemoryProImportResult,
    QueryDeps,
    ReadResult,
    StatusResult,
    VerificationDeps,
    VerifyResult,
)
from clawops.hypermemory.models import (
    FusionMode,
    ReflectionMode,
    ReindexSummary,
    SearchBackend,
    SearchHit,
    SearchMode,
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
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.canonical_store_service import CanonicalStoreService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.services.indexing_service import IndexingService
from clawops.hypermemory.services.query_service import QueryService
from clawops.hypermemory.services.verification_service import VerificationService


@dataclass(slots=True)
class _IndexingDepsImpl(IndexingDeps):
    flush_metadata_callback: Callable[[], FlushMetadataResult]

    def flush_metadata(self) -> FlushMetadataResult:
        return self.flush_metadata_callback()


@dataclass(slots=True)
class _QueryDepsImpl(QueryDeps):
    indexing: IndexingService
    reindex_callback: Callable[[bool], ReindexSummary]
    fact_lookup_callback: Callable[[str, sqlite3.Connection | None, str | None], SearchHit | None]

    def iter_documents(self):
        return self.indexing.iter_documents()

    def missing_corpus_paths(self):
        return self.indexing.missing_corpus_paths()

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        return self.reindex_callback(flush_metadata)

    def get_fact(
        self,
        fact_key: str,
        *,
        conn: sqlite3.Connection | None = None,
        scope: str | None = None,
    ) -> SearchHit | None:
        return self.fact_lookup_callback(fact_key, conn, scope)


@dataclass(slots=True)
class _VerificationDepsImpl(VerificationDeps):
    status_callback: Callable[[], StatusResult]
    missing_required_corpus_paths_callback: Callable[[], list[CorpusPathStatus]]

    def status(self) -> StatusResult:
        return self.status_callback()

    def missing_required_corpus_paths(self):
        return self.missing_required_corpus_paths_callback()


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
        self._vector_backend: VectorBackend = (
            vector_backend if vector_backend is not None else QdrantBackend(config.qdrant)
        )

        canonical_store_service: CanonicalStoreService | None = None
        query_service: QueryService | None = None

        def flush_metadata_callback() -> FlushMetadataResult:
            if canonical_store_service is None:
                raise RuntimeError("canonical store is not initialized")
            return canonical_store_service.flush_metadata()

        def fact_lookup_callback(
            fact_key: str,
            conn: sqlite3.Connection | None,
            scope: str | None,
        ) -> SearchHit | None:
            if canonical_store_service is None:
                raise RuntimeError("canonical store is not initialized")
            return canonical_store_service.get_fact(fact_key, conn=conn, scope=scope)

        def query_status_callback() -> StatusResult:
            if query_service is None:
                raise RuntimeError("query service is not initialized")
            return query_service.status()

        self.index = IndexService(connect=self.connect)
        self.backend = BackendService(
            config=self.config,
            embedding_provider=self._embedding_provider,
            vector_backend=self._vector_backend,
            index=self.index,
        )
        self.indexing = IndexingService(
            config=self.config,
            connect=self.connect,
            backend=self.backend,
            index=self.index,
            deps=_IndexingDepsImpl(flush_metadata_callback=flush_metadata_callback),
        )
        self.verification = VerificationService(
            config=self.config,
            connect=self.connect,
            backend=self.backend,
            vector_backend=self._vector_backend,
            rerank_provider=self._rerank_provider,
            deps=_VerificationDepsImpl(
                status_callback=query_status_callback,
                missing_required_corpus_paths_callback=self.indexing.missing_required_corpus_paths,
            ),
        )
        query_service = QueryService(
            config=self.config,
            connect=self.connect,
            backend=self.backend,
            index=self.index,
            vector_backend=self._vector_backend,
            rerank_scorer=self.verification.observed_rerank_scorer,
            rerank_device_resolver=self.verification.rerank_resolved_device,
            deps=_QueryDepsImpl(
                indexing=self.indexing,
                reindex_callback=lambda flush_metadata: self.indexing.reindex(
                    flush_metadata=flush_metadata
                ),
                fact_lookup_callback=fact_lookup_callback,
            ),
        )
        self.query = query_service
        canonical_store_service = CanonicalStoreService(
            config=self.config,
            deps=self,
        )
        self.canonical_store = canonical_store_service

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        ensure_parent(self.config.db_path)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        return conn

    def status(self) -> StatusResult:
        """Return index and governance status."""
        return self.query.status()

    def is_dirty(self) -> bool:
        """Return whether the derived index differs from canonical Markdown files."""
        return self.query.is_dirty()

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
        return self.indexing.reindex(flush_metadata=flush_metadata)

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
        return self.query.search(
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

    def verify(self) -> VerifyResult:
        """Verify the supported sparse+dense backend contract for hypermemory."""
        return self.verification.verify()

    def read(
        self,
        rel_path: str,
        *,
        from_line: int | None = None,
        lines: int | None = None,
    ) -> ReadResult:
        """Read a canonical file returned by the memory index."""
        return self.query.read(rel_path, from_line=from_line, lines=lines)

    def export_memory_pro_import(
        self,
        *,
        scope: str | None = None,
        include_daily: bool = False,
        auto_index: bool = True,
    ) -> MemoryProImportResult:
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

    def flush_metadata(self) -> FlushMetadataResult:
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

    def benchmark_cases(self, cases: list[dict[str, Any]]) -> BenchmarkResult:
        """Run simple benchmark cases against the current engine."""
        return self.canonical_store.benchmark_cases(cases)
