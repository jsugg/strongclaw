"""Core engine for StrongClaw hypermemory."""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any, Literal, cast

from clawops.common import ensure_parent
from clawops.hypermemory.capture import (
    CaptureCandidate,
    extract_candidates_llm,
    extract_candidates_regex,
    resolve_capture_api_key,
)
from clawops.hypermemory.config import HypermemoryConfig, matches_glob, resolve_under_workspace
from clawops.hypermemory.governance import ensure_writable_scope, should_auto_apply, validate_scope
from clawops.hypermemory.lifecycle import TierManager, compute_decay_score
from clawops.hypermemory.models import (
    DenseSearchCandidate,
    EvidenceEntry,
    FactCategory,
    FusionMode,
    IndexedDocument,
    Lane,
    ProposalRecord,
    ReflectionMode,
    ReflectionSummary,
    ReindexSummary,
    RerankResponse,
    SearchBackend,
    SearchHit,
    SearchMode,
    SparseSearchCandidate,
    Tier,
)
from clawops.hypermemory.noise import is_noise
from clawops.hypermemory.parser import build_document, iter_retained_notes, parse_typed_entry
from clawops.hypermemory.providers import create_embedding_provider, create_rerank_provider
from clawops.hypermemory.qdrant_backend import QdrantBackend
from clawops.hypermemory.retrieval import (
    _adaptive_pool_size,
    _count_active_items,
    _count_items,
    _estimate_query_specificity,
    _invalidated_ratio,
    search_index,
)
from clawops.hypermemory.schema import SCHEMA_VERSION, ensure_schema
from clawops.hypermemory.sparse import SparseEncoder, build_sparse_encoder
from clawops.observability import TelemetryValue, emit_structured_log, observed_span

BANK_HEADERS = {
    "fact": "# World Model\n\n## Entries\n",
    "reflection": "# Experience\n\n## Entries\n",
    "opinion": "# Opinions\n\n## Entries\n",
}
WRITABLE_PREFIXES = ("memory/", "bank/")
MEMORY_PRO_CATEGORY_MAP = {
    "fact": "fact",
    "reflection": "other",
    "opinion": "preference",
    "entity": "entity",
}
MEMORY_PRO_IMPORTANCE_MAP = {
    "fact": 0.8,
    "reflection": 0.7,
    "opinion": 0.65,
    "entity": 0.75,
}
FACT_KEY_INFERENCE_RULES: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (re.compile(r"(?i)\b(?:my|the user'?s?) name is\b"), "user:name"),
    (re.compile(r"(?i)\b(?:my|the user'?s?) timezone is\b"), "user:timezone"),
    (re.compile(r"(?i)\b(?:my|the user'?s?) role is\b"), "user:role"),
    (re.compile(r"(?i)\b(?:my|the user'?s?) team is\b"), "user:team"),
    (
        re.compile(
            r"(?i)\b(?:I|the user) (?:use|prefer)s?\s+.+?\s+(?:as\s+|for\s+)?(?:my\s+|their\s+)?editor\b"
        ),
        "pref:editor",
    ),
    (
        re.compile(r"(?i)\b(?:I|the user) (?:use|prefer)s?\s+.+?\s+(?:theme|mode)\b"),
        "pref:theme",
    ),
    (
        re.compile(
            r"(?i)\b(?:I|the user) (?:prefer|use)s?\s+.+?\s+(?:for|as)\s+(?:my\s+)?(?:primary\s+)?(?:programming\s+)?language\b"
        ),
        "pref:language",
    ),
    (
        re.compile(
            r"(?i)\b(?:we|the team) (?:decided|chose|agreed) to use .+? (?:for|as) (?:the\s+|our\s+)?(.+)$"
        ),
        None,
    ),
)
FACT_QUERY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:my|user) timezone\b"), "user:timezone"),
    (re.compile(r"(?i)\b(?:my|user) name\b"), "user:name"),
    (re.compile(r"(?i)\b(?:my|user) role\b"), "user:role"),
    (re.compile(r"(?i)\b(?:my|user) team\b"), "user:team"),
    (re.compile(r"(?i)\b(?:my|user) editor\b"), "pref:editor"),
    (re.compile(r"(?i)\b(?:my|user) theme\b"), "pref:theme"),
    (re.compile(r"(?i)\b(?:my|user) language\b"), "pref:language"),
)


class HypermemoryEngine:
    """Markdown-canonical memory engine with a derived SQLite index."""

    def __init__(self, config: HypermemoryConfig) -> None:
        self.config = config
        self._embedding_provider = create_embedding_provider(config.embedding)
        self._rerank_provider = create_rerank_provider(config.rerank)
        self._qdrant_backend = QdrantBackend(config.qdrant)

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        ensure_parent(self.config.db_path)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        return conn

    def status(self) -> dict[str, Any]:
        """Return index and governance status."""
        sparse_fingerprint_current: str | None = None
        rerank_resolved_device = self._rerank_resolved_device()
        missing_corpus_paths = self._missing_corpus_paths()
        with self.connect() as conn:
            document_count = self._count_rows(conn, "documents")
            item_count = self._count_rows(conn, "search_items")
            vector_items = self._count_rows(conn, "vector_items")
            sparse_vector_items = self._count_sparse_vector_items(conn)
            sparse_vocabulary_size = self._count_rows(conn, "sparse_terms")
            facts = self._count_rows(conn, "facts")
            opinions = self._count_rows(conn, "opinions")
            reflections = self._count_rows(conn, "reflections")
            entities = self._count_rows(conn, "entities")
            proposals = self._count_rows(conn, "proposals")
            conflicts = self._count_rows(conn, "conflicts")
            fact_registry_entries = self._count_rows(conn, "fact_registry")
            backend_fingerprint = self._backend_state_value(conn, "config_fingerprint")
            last_sync_at = self._backend_state_value(conn, "last_sync_at")
            last_sync_error = self._backend_state_value(conn, "last_sync_error")
            sparse_fingerprint = self._backend_state_value(conn, "sparse_fingerprint")
            sparse_doc_count = self._backend_state_value(conn, "sparse_doc_count")
            sparse_avg_doc_length = self._backend_state_value(conn, "sparse_avg_doc_length")
            if self._backend_uses_sparse_vectors():
                sparse_fingerprint_current = self._current_sparse_fingerprint()
        qdrant_health = self._qdrant_backend.health()
        return {
            "ok": True,
            "provider": "strongclaw-hypermemory",
            "schemaVersion": SCHEMA_VERSION,
            "workspaceRoot": self.config.workspace_root.as_posix(),
            "dbPath": self.config.db_path.as_posix(),
            "dirty": self.is_dirty(),
            "backendActive": self.config.backend.active,
            "backendFallback": self.config.backend.fallback,
            "backendConfigDirty": backend_fingerprint != self._backend_fingerprint(),
            "documents": document_count,
            "searchItems": item_count,
            "vectorItems": vector_items,
            "sparseVectorItems": sparse_vector_items,
            "sparseVocabularySize": sparse_vocabulary_size,
            "facts": facts,
            "opinions": opinions,
            "reflections": reflections,
            "entities": entities,
            "proposals": proposals,
            "conflicts": conflicts,
            "factRegistryEntries": fact_registry_entries,
            "embeddingEnabled": self.config.embedding.enabled,
            "embeddingProvider": self.config.embedding.provider,
            "embeddingModel": self.config.embedding.model,
            "rerankEnabled": self.config.rerank.enabled,
            "rerankProvider": self.config.rerank.provider,
            "rerankFallbackProvider": self.config.rerank.fallback_provider,
            "rerankFailOpen": self.config.rerank.fail_open,
            "rerankModel": self.config.rerank.model_for(),
            "rerankDevice": self.config.rerank.local.device,
            "rerankResolvedDevice": rerank_resolved_device,
            "rerankFallbackModel": self.config.rerank.model_for(
                self.config.rerank.fallback_provider
            ),
            "rerankCandidatePool": self.config.hybrid.rerank_candidate_pool,
            "rerankOperationalRequired": (
                self.config.rerank.enabled and self.config.hybrid.rerank_candidate_pool > 0
            ),
            "qdrantEnabled": bool(qdrant_health.get("enabled", False)),
            "qdrantHealthy": bool(qdrant_health.get("healthy", False)),
            "qdrant": qdrant_health,
            "lastVectorSyncAt": last_sync_at,
            "lastVectorSyncError": last_sync_error,
            "sparseFingerprint": sparse_fingerprint,
            "sparseFingerprintDirty": (
                sparse_fingerprint_current is not None
                and sparse_fingerprint != sparse_fingerprint_current
            ),
            "sparseDocumentCount": int(sparse_doc_count or "0"),
            "sparseAverageDocumentLength": float(sparse_avg_doc_length or "0"),
            "defaultScope": self.config.governance.default_scope,
            "readableScopes": list(self.config.governance.readable_scope_patterns),
            "writableScopes": list(self.config.governance.writable_scope_patterns),
            "autoApplyScopes": list(self.config.governance.auto_apply_scope_patterns),
            "missingCorpusPaths": missing_corpus_paths,
        }

    def is_dirty(self) -> bool:
        """Return whether the derived index differs from canonical Markdown files."""
        with self.connect() as conn:
            existing = {
                str(row["rel_path"]): str(row["sha256"])
                for row in conn.execute("SELECT rel_path, sha256 FROM documents")
            }
            backend_fingerprint = self._backend_state_value(conn, "config_fingerprint")
            sparse_fingerprint = self._backend_state_value(conn, "sparse_fingerprint")
        documents = list(self._iter_documents())
        current = {document.rel_path: document.sha256 for document in documents}
        sparse_dirty = False
        if self._backend_uses_sparse_vectors():
            sparse_dirty = sparse_fingerprint != self._sparse_fingerprint_for_documents(documents)
        return (
            current != existing
            or backend_fingerprint != self._backend_fingerprint()
            or sparse_dirty
        )

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
        if flush_metadata:
            self.flush_metadata()
        documents = list(self._iter_documents())
        sparse_encoder = self._sparse_encoder_for_documents(documents)
        typed_counts: defaultdict[str, int] = defaultdict(int)
        vector_rows: list[dict[str, Any]] = []
        with observed_span(
            "clawops.hypermemory.reindex",
            attributes={
                "documents": len(documents),
                "backend": self.config.backend.active,
                "qdrant_enabled": self.config.qdrant.enabled,
            },
        ) as span:
            try:
                with self.connect() as conn:
                    existing = {
                        str(row["rel_path"]): str(row["sha256"])
                        for row in conn.execute("SELECT rel_path, sha256 FROM documents")
                    }
                    existing_point_ids = {
                        str(row["point_id"])
                        for row in conn.execute("SELECT point_id FROM vector_items")
                    }
                    current = {document.rel_path: document.sha256 for document in documents}
                    dirty = current != existing
                    if self._backend_uses_sparse_vectors():
                        dirty = dirty or (
                            self._backend_state_value(conn, "sparse_fingerprint")
                            != sparse_encoder.fingerprint
                        )
                    self._clear_derived_rows(conn)
                    indexed_at = datetime.now(tz=UTC).isoformat()
                    chunks = 0
                    for document in documents:
                        doc_cursor = conn.execute(
                            """
                            INSERT INTO documents (
                                rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                document.rel_path,
                                document.abs_path.as_posix(),
                                document.lane,
                                document.source_name,
                                document.sha256,
                                document.line_count,
                                document.modified_at,
                                indexed_at,
                            ),
                        )
                        document_id = doc_cursor.lastrowid
                        if document_id is None:
                            raise RuntimeError("document insert did not return a rowid")
                        for item in document.items:
                            evidence = self._evidence_entries(
                                document.rel_path,
                                item.start_line,
                                item.end_line,
                                item.evidence,
                            )
                            item_cursor = conn.execute(
                                """
                                INSERT INTO search_items (
                                    document_id,
                                    rel_path,
                                    lane,
                                    source_name,
                                    source_kind,
                                    item_type,
                                    title,
                                    snippet,
                                    normalized_text,
                                    start_line,
                                    end_line,
                                    confidence,
                                    scope,
                                    modified_at,
                                    contradiction_count,
                                    evidence_count,
                                    entities_json,
                                    evidence_json,
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
                                )
                                VALUES (
                                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                                )
                                """,
                                (
                                    document_id,
                                    document.rel_path,
                                    document.lane,
                                    document.source_name,
                                    "durable" if document.lane == "memory" else "corpus",
                                    item.item_type,
                                    item.title,
                                    item.snippet,
                                    self._normalized_retrieval_text(item.title, item.snippet),
                                    item.start_line,
                                    item.end_line,
                                    item.confidence,
                                    item.scope,
                                    document.modified_at,
                                    len(item.contradicts),
                                    len(evidence),
                                    json.dumps(list(item.entities), sort_keys=True),
                                    json.dumps(
                                        [entry.to_dict() for entry in evidence], sort_keys=True
                                    ),
                                    item.importance,
                                    item.tier,
                                    item.access_count,
                                    item.last_access_date,
                                    item.injected_count,
                                    item.confirmed_count,
                                    item.bad_recall_count,
                                    item.fact_key,
                                    item.invalidated_at,
                                    item.supersedes,
                                ),
                            )
                            item_row_id = item_cursor.lastrowid
                            if item_row_id is None:
                                raise RuntimeError("search item insert did not return a rowid")
                            conn.execute(
                                "INSERT INTO search_items_fts(rowid, title, snippet, entities) VALUES (?, ?, ?, ?)",
                                (item_row_id, item.title, item.snippet, " ".join(item.entities)),
                            )
                            self._insert_typed_row(
                                conn=conn,
                                item_id=item_row_id,
                                document_rel_path=document.rel_path,
                                item=item,
                                typed_counts=typed_counts,
                                evidence=evidence,
                            )
                            vector_rows.append(
                                {
                                    "item_id": int(item_row_id),
                                    "point_id": self._point_id(
                                        document_rel_path=document.rel_path,
                                        item_type=item.item_type,
                                        start_line=item.start_line,
                                        end_line=item.end_line,
                                        snippet=item.snippet,
                                    ),
                                    "content": self._normalized_retrieval_text(
                                        item.title, item.snippet
                                    ),
                                    "payload": {
                                        "item_id": int(item_row_id),
                                        "rel_path": document.rel_path,
                                        "lane": document.lane,
                                        "source_name": document.source_name,
                                        "item_type": item.item_type,
                                        "scope": item.scope,
                                        "start_line": item.start_line,
                                        "end_line": item.end_line,
                                        "modified_at": document.modified_at,
                                        "confidence": item.confidence,
                                    },
                                }
                            )
                            chunks += 1
                    self._rebuild_fact_registry(conn)
                    conn.commit()
                    self._sync_dense_backend(
                        conn=conn,
                        vector_rows=vector_rows,
                        stale_point_ids=existing_point_ids,
                        sparse_encoder=sparse_encoder,
                    )
                summary = ReindexSummary(
                    files=len(documents),
                    chunks=chunks,
                    dirty=dirty,
                    facts=typed_counts["fact"],
                    opinions=typed_counts["opinion"],
                    reflections=typed_counts["reflection"],
                    entities=typed_counts["entity"],
                    proposals=typed_counts["proposal"],
                )
                summary_payload = {
                    "files": summary.files,
                    "chunks": summary.chunks,
                    "dirty": summary.dirty,
                    "vector_rows": len(vector_rows),
                    "facts": summary.facts,
                    "opinions": summary.opinions,
                    "reflections": summary.reflections,
                    "entities": summary.entities,
                    "proposals": summary.proposals,
                }
                span.set_attributes(summary_payload)
                emit_structured_log("clawops.hypermemory.reindex", summary_payload)
                return summary
            except Exception as err:
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.reindex.error",
                    {
                        "documents": len(documents),
                        "backend": self.config.backend.active,
                        "error": str(err),
                    },
                )
                raise

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
        if auto_index and self.is_dirty():
            self.reindex()
        limit = max_results if max_results is not None else self.config.default_max_results
        if limit <= 0:
            raise ValueError("max_results must be positive")
        requested_backend = backend or self.config.backend.active
        hybrid_config = replace(
            self.config.hybrid,
            dense_candidate_pool=(
                dense_candidate_pool
                if dense_candidate_pool is not None
                else self.config.hybrid.dense_candidate_pool
            ),
            sparse_candidate_pool=(
                sparse_candidate_pool
                if sparse_candidate_pool is not None
                else self.config.hybrid.sparse_candidate_pool
            ),
            fusion=cast(FusionMode, fusion or self.config.hybrid.fusion),
        )
        with observed_span(
            "clawops.hypermemory.search",
            attributes={
                "backend": requested_backend,
                "lane": lane,
                "scope": scope,
                "max_results": limit,
                "include_explain": include_explain,
            },
        ) as span:
            try:
                resolved_backend = requested_backend
                dense_candidates: list[DenseSearchCandidate] = []
                sparse_candidates: list[SparseSearchCandidate] = []
                qdrant_dense_search_ms = 0.0
                qdrant_sparse_search_ms = 0.0
                fallback_activated = False
                with self.connect() as conn:
                    if self.config.fact_registry.enabled and not include_invalidated:
                        exact_hit = self._exact_fact_lookup(conn, query=query)
                        if exact_hit is not None:
                            return [exact_hit]
                    if self.config.retrieval.adaptive_pool:
                        query_specificity = _estimate_query_specificity(query)
                        total_items = _count_items(conn)
                        active_items = _count_active_items(conn)
                        invalidated_ratio = _invalidated_ratio(conn)
                        hybrid_config = replace(
                            hybrid_config,
                            dense_candidate_pool=_adaptive_pool_size(
                                base_pool=hybrid_config.dense_candidate_pool,
                                total_items=total_items,
                                active_items=active_items,
                                invalidated_ratio=invalidated_ratio,
                                query_specificity=query_specificity,
                                has_scope_filter=scope is not None,
                                max_multiplier=self.config.retrieval.adaptive_pool_max_multiplier,
                            ),
                            sparse_candidate_pool=_adaptive_pool_size(
                                base_pool=hybrid_config.sparse_candidate_pool,
                                total_items=total_items,
                                active_items=active_items,
                                invalidated_ratio=invalidated_ratio,
                                query_specificity=query_specificity,
                                has_scope_filter=scope is not None,
                                max_multiplier=self.config.retrieval.adaptive_pool_max_multiplier,
                            ),
                        )
                    if resolved_backend in {
                        "qdrant_dense_hybrid",
                        "qdrant_sparse_dense_hybrid",
                    }:
                        try:
                            dense_candidates, qdrant_dense_search_ms = self._dense_search(
                                query=query,
                                lane=lane,
                                scope=scope,
                                candidate_limit=hybrid_config.dense_candidate_pool,
                            )
                            if resolved_backend == "qdrant_sparse_dense_hybrid":
                                sparse_candidates, qdrant_sparse_search_ms = self._sparse_search(
                                    conn=conn,
                                    query=query,
                                    lane=lane,
                                    scope=scope,
                                    candidate_limit=hybrid_config.sparse_candidate_pool,
                                )
                        except Exception as err:
                            if self.config.backend.fallback != "sqlite_fts":
                                raise
                            resolved_backend = self.config.backend.fallback
                            fallback_activated = True
                            dense_candidates = []
                            sparse_candidates = []
                            emit_structured_log(
                                "clawops.hypermemory.search.fallback",
                                {
                                    "requestedBackend": requested_backend,
                                    "resolvedBackend": resolved_backend,
                                    "error": str(err),
                                },
                            )
                    hits, diagnostics = search_index(
                        conn,
                        query=query,
                        max_results=limit,
                        min_score=min_score,
                        mode=lane,
                        scope=scope,
                        ranking=self.config.ranking,
                        hybrid=hybrid_config,
                        decay=self.config.decay,
                        feedback=self.config.feedback,
                        retrieval=self.config.retrieval,
                        dense_candidates=dense_candidates,
                        sparse_candidates=sparse_candidates,
                        active_backend=resolved_backend,
                        rerank_scorer=self._observed_rerank_scorer(),
                        rerank_candidate_pool=hybrid_config.rerank_candidate_pool,
                        include_explain=include_explain,
                    )
                    if self.config.fact_registry.enabled and not include_invalidated:
                        hits = self._filter_current_fact_hits(conn, hits)
                    if include_invalidated:
                        invalidated_hits = self._search_invalidated_hits(
                            conn,
                            query=query,
                            lane=lane,
                            scope=scope,
                            limit=limit,
                        )
                        seen_item_ids = {
                            hit.item_id for hit in invalidated_hits if hit.item_id is not None
                        }
                        hits = invalidated_hits + [
                            hit
                            for hit in hits
                            if hit.item_id is None or hit.item_id not in seen_item_ids
                        ]
                        hits = hits[:limit]
                diagnostics = replace(
                    diagnostics,
                    qdrant_dense_ms=qdrant_dense_search_ms,
                    qdrant_sparse_ms=qdrant_sparse_search_ms,
                )
                telemetry_payload: dict[str, Any] = {
                    "requestedBackend": requested_backend,
                    "resolvedBackend": resolved_backend,
                    "fallbackActivated": fallback_activated,
                    "results": len(hits),
                }
                telemetry_payload.update(diagnostics.to_dict())
                span.set_attributes(telemetry_payload)
                emit_structured_log("clawops.hypermemory.search", telemetry_payload)
                return hits
            except Exception as err:
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.search.error",
                    {
                        "backend": requested_backend,
                        "lane": lane,
                        "scope": scope,
                        "error": str(err),
                    },
                )
                raise

    def _observed_rerank_scorer(self) -> Callable[[str, Sequence[str]], RerankResponse]:
        """Return a rerank scorer that emits telemetry and honors fail-open semantics."""

        def score(query: str, documents: Sequence[str]) -> RerankResponse:
            if not documents:
                return RerankResponse()
            with observed_span(
                "clawops.hypermemory.rerank",
                attributes={
                    "configuredProvider": self.config.rerank.provider,
                    "fallbackProvider": self.config.rerank.fallback_provider,
                    "configuredDevice": self.config.rerank.local.device,
                    "resolvedDevice": self._rerank_resolved_device(),
                    "candidateCount": len(documents),
                },
            ) as span:
                started_at = perf_counter()
                try:
                    response = self._rerank_provider.score(query, documents)
                except Exception as err:
                    latency_ms = (perf_counter() - started_at) * 1000.0
                    error_payload: dict[str, TelemetryValue] = {
                        "configuredProvider": self.config.rerank.provider,
                        "fallbackProvider": self.config.rerank.fallback_provider,
                        "configuredDevice": self.config.rerank.local.device,
                        "resolvedDevice": self._rerank_resolved_device(),
                        "candidateCount": len(documents),
                        "rerankMs": round(latency_ms, 3),
                        "error": str(err),
                    }
                    span.record_exception(err)
                    span.set_error(str(err))
                    span.set_attributes(error_payload)
                    emit_structured_log("clawops.hypermemory.rerank.error", error_payload)
                    if not self.config.rerank.fail_open:
                        raise
                    return RerankResponse(
                        latency_ms=latency_ms,
                        fail_open=True,
                        error=str(err),
                    )
                latency_ms = (perf_counter() - started_at) * 1000.0
                observed_response = replace(response, latency_ms=latency_ms)
                payload: dict[str, Any] = {
                    "configuredProvider": self.config.rerank.provider,
                    "provider": observed_response.provider,
                    "fallbackProvider": self.config.rerank.fallback_provider,
                    "configuredDevice": self.config.rerank.local.device,
                    "resolvedDevice": self._rerank_resolved_device(),
                    "fallbackUsed": observed_response.fallback_used,
                    "applied": observed_response.applied,
                    "failOpen": observed_response.fail_open,
                    "candidateCount": len(documents),
                    "rerankMs": round(latency_ms, 3),
                }
                if observed_response.error:
                    payload["error"] = observed_response.error
                span.set_attributes(payload)
                emit_structured_log("clawops.hypermemory.rerank", payload)
                return observed_response

        return score

    def _rerank_resolved_device(self) -> str:
        """Return the current runtime device selected for reranking, if available."""
        resolver = getattr(self._rerank_provider, "resolved_device", None)
        if not callable(resolver):
            return ""
        try:
            return cast(str, resolver())
        except Exception:
            return ""

    def _rerank_probe_documents(self, conn: sqlite3.Connection, *, limit: int = 2) -> list[str]:
        """Return a small set of indexed snippets for rerank verification."""
        rows = conn.execute(
            """
            SELECT rel_path, snippet
            FROM search_items
            WHERE trim(snippet) <> ''
            ORDER BY CASE lane WHEN 'memory' THEN 0 ELSE 1 END, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [f"{row['rel_path']}\n{row['snippet']}" for row in rows]

    def _verify_rerank_provider(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> dict[str, Any]:
        """Verify that the configured rerank provider returns usable scores."""
        started_at = perf_counter()
        response = self._rerank_provider.score(query, documents)
        latency_ms = (perf_counter() - started_at) * 1000.0
        if not response.applied:
            raise RuntimeError("rerank provider returned no applied scores")
        if response.provider == "none":
            raise RuntimeError("rerank provider resolved to none")
        if len(response.scores) != len(documents):
            raise RuntimeError(
                "rerank provider returned "
                f"{len(response.scores)} scores for {len(documents)} documents"
            )
        if any(not math.isfinite(score) for score in response.scores):
            raise RuntimeError("rerank provider returned a non-finite score")
        return {
            "provider": response.provider,
            "fallbackUsed": response.fallback_used,
            "candidateCount": len(documents),
            "rerankMs": round(latency_ms, 3),
        }

    def verify(self) -> dict[str, Any]:
        """Verify the supported sparse+dense backend contract for hypermemory."""
        errors: list[str] = []
        lane_checks: dict[str, Any] = {}
        if self.config.backend.active != "qdrant_sparse_dense_hybrid":
            errors.append(
                "backend.active must be qdrant_sparse_dense_hybrid for hypermemory verification"
            )
        status = self.status()
        missing_required_paths = self._missing_required_corpus_paths()
        if missing_required_paths:
            names = ", ".join(str(entry["name"]) for entry in missing_required_paths)
            errors.append(f"required corpus paths are missing: {names}")
        if status["dirty"]:
            errors.append("hypermemory index is dirty")
        if status["lastVectorSyncError"]:
            errors.append(f"vector sync error: {status['lastVectorSyncError']}")
        if not status["qdrantEnabled"] or not status["qdrantHealthy"]:
            errors.append("Qdrant must be enabled and healthy")
        if int(status["vectorItems"]) <= 0:
            errors.append("no dense vector items are indexed")
        if int(status["sparseVectorItems"]) <= 0:
            errors.append("no sparse vector items are indexed")
        if bool(status["sparseFingerprintDirty"]):
            errors.append("sparse fingerprint is dirty")

        collection_details: dict[str, Any] = {}
        if status["qdrantEnabled"] and status["qdrantHealthy"]:
            try:
                collection_details = self._qdrant_backend.collection_details()
            except Exception as err:
                errors.append(f"unable to read Qdrant collection details: {err}")
            else:
                if not self._collection_has_hypermemory_vector_lanes(collection_details):
                    errors.append(
                        "Qdrant collection is missing the named dense or sparse vector lane"
                    )

        with self.connect() as conn:
            probe_query = self._hypermemory_probe_query(conn)
            lane_checks["probeQuery"] = probe_query or ""
            if not probe_query:
                errors.append("unable to build a hypermemory probe query from indexed content")
            else:
                try:
                    dense_hits, dense_ms = self._dense_search(
                        query=probe_query,
                        lane="all",
                        scope=None,
                        candidate_limit=self.config.hybrid.dense_candidate_pool,
                    )
                    lane_checks["dense"] = {"hits": len(dense_hits), "ms": round(dense_ms, 3)}
                    if not dense_hits:
                        errors.append("dense lane returned no candidates")
                except Exception as err:
                    errors.append(f"dense lane failed: {err}")
                try:
                    sparse_hits, sparse_ms = self._sparse_search(
                        conn=conn,
                        query=probe_query,
                        lane="all",
                        scope=None,
                        candidate_limit=self.config.hybrid.sparse_candidate_pool,
                    )
                    lane_checks["sparse"] = {"hits": len(sparse_hits), "ms": round(sparse_ms, 3)}
                    if not sparse_hits:
                        errors.append("sparse lane returned no candidates")
                except Exception as err:
                    errors.append(f"sparse lane failed: {err}")
                rerank_required = (
                    self.config.rerank.enabled and self.config.hybrid.rerank_candidate_pool > 0
                )
                rerank_check: dict[str, Any] = {
                    "required": rerank_required,
                    "candidatePool": self.config.hybrid.rerank_candidate_pool,
                }
                lane_checks["rerank"] = rerank_check
                if rerank_required:
                    probe_documents = self._rerank_probe_documents(
                        conn,
                        limit=min(self.config.hybrid.rerank_candidate_pool, 2),
                    )
                    rerank_check["documents"] = len(probe_documents)
                    if not probe_documents:
                        errors.append("unable to build rerank probe documents from indexed content")
                    else:
                        try:
                            rerank_check.update(
                                self._verify_rerank_provider(
                                    query=probe_query,
                                    documents=probe_documents,
                                )
                            )
                        except Exception as err:
                            errors.append(f"rerank provider failed: {err}")

        return {
            "ok": not errors,
            "provider": "strongclaw-hypermemory",
            "backend": self.config.backend.active,
            "status": status,
            "collection": collection_details,
            "laneChecks": lane_checks,
            "errors": errors,
        }

    def read(
        self,
        rel_path: str,
        *,
        from_line: int | None = None,
        lines: int | None = None,
    ) -> dict[str, Any]:
        """Read a canonical file returned by the memory index."""
        path = self._resolve_read_path(rel_path)
        if not path.exists():
            return {"path": rel_path, "text": ""}
        content = path.read_text(encoding="utf-8")
        if from_line is None:
            return {"path": rel_path, "text": content}
        if from_line <= 0:
            raise ValueError("from_line must be positive")
        line_count = lines if lines is not None else 20
        if line_count <= 0:
            raise ValueError("lines must be positive")
        raw_lines = content.splitlines()
        start_index = from_line - 1
        return {
            "path": rel_path,
            "text": "\n".join(raw_lines[start_index : start_index + line_count]),
        }

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
                        "id": f"strongclaw-hypermemory:{self._sha256(source_fingerprint)}",
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
        """Append a durable memory entry to the appropriate canonical Markdown file."""
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
        """Replace text inside a writable memory file."""
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
        """Promote retained daily-log entries into durable bank pages via proposals."""
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
                    Literal["fact", "reflection", "opinion", "entity"], note_kind
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
                        target, kind=proposal.kind, entry_line=proposal.entry_line
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
        """Extract and store durable memory candidates from conversation messages."""
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
                kind=cast(Literal["fact", "reflection", "opinion", "entity"], candidate.kind),
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
        """Invalidate or delete a durable memory entry."""
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
        """Store a new entry that supersedes an existing durable entry."""
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
        """Flush lifecycle metadata from SQLite rows back into canonical Markdown."""
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
        """Evaluate lifecycle scores and promote or demote tiers."""
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
        return self._sha256(entry_line.strip())[:8]

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

    def _row_to_search_hit(self, row: sqlite3.Row) -> SearchHit:
        """Convert a SQLite row into a search hit payload."""
        row_keys = set(row.keys())
        entities_json = row["entities_json"] if "entities_json" in row_keys else "[]"
        entities = tuple(json.loads(str(entities_json))) if entities_json is not None else ()
        return SearchHit(
            item_id=int(row["id"]) if "id" in row_keys and row["id"] is not None else None,
            path=str(row["rel_path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            score=1.0,
            snippet=str(row["snippet"]),
            lane=cast(Lane, str(row["lane"])) if "lane" in row_keys else "memory",
            item_type=str(row["item_type"]) if "item_type" in row_keys else "fact",
            confidence=(
                None
                if "confidence" not in row_keys or row["confidence"] is None
                else float(row["confidence"])
            ),
            entities=entities,
            scope=str(row["scope"]) if "scope" in row_keys else None,
            evidence_count=int(row["evidence_count"]) if "evidence_count" in row_keys else 0,
            contradiction_count=(
                int(row["contradiction_count"]) if "contradiction_count" in row_keys else 0
            ),
            backend="sqlite_fts",
            importance=(
                None
                if "importance" not in row_keys or row["importance"] is None
                else float(row["importance"])
            ),
            tier=(
                self._normalize_tier(str(row["tier"]))
                if "tier" in row_keys and row["tier"] is not None
                else "working"
            ),
            access_count=int(row["access_count"]) if "access_count" in row_keys else 0,
            last_access_date=(
                None
                if "last_access_date" not in row_keys or row["last_access_date"] is None
                else str(row["last_access_date"])
            ),
            injected_count=int(row["injected_count"]) if "injected_count" in row_keys else 0,
            confirmed_count=int(row["confirmed_count"]) if "confirmed_count" in row_keys else 0,
            bad_recall_count=(
                int(row["bad_recall_count"]) if "bad_recall_count" in row_keys else 0
            ),
            fact_key=(
                None
                if "fact_key" not in row_keys or row["fact_key"] is None
                else str(row["fact_key"])
            ),
            invalidated_at=(
                None
                if "invalidated_at" not in row_keys or row["invalidated_at"] is None
                else str(row["invalidated_at"])
            ),
            supersedes=(
                None
                if "supersedes" not in row_keys or row["supersedes"] is None
                else str(row["supersedes"])
            ),
        )

    def _rebuild_fact_registry(self, conn: sqlite3.Connection) -> None:
        """Rebuild the current-state fact registry from indexed search items."""
        conn.execute("DELETE FROM fact_registry")
        if not self.config.fact_registry.enabled:
            return
        rows = conn.execute("""
            SELECT id, fact_key, rel_path, start_line, modified_at
            FROM search_items
            WHERE fact_key IS NOT NULL
              AND invalidated_at IS NULL
            ORDER BY rel_path ASC, start_line ASC, id ASC
            """).fetchall()
        registry: dict[str, dict[str, Any]] = {}
        for row in rows:
            fact_key = str(row["fact_key"])
            current = registry.get(fact_key)
            if current is None:
                registry[fact_key] = {
                    "current_item_id": int(row["id"]),
                    "history": [],
                    "last_updated": str(row["modified_at"]),
                }
                continue
            current["history"].append(current["current_item_id"])
            current["current_item_id"] = int(row["id"])
            current["last_updated"] = str(row["modified_at"])
        for fact_key, entry in registry.items():
            conn.execute(
                """
                INSERT INTO fact_registry(
                    fact_key,
                    current_item_id,
                    category,
                    last_updated,
                    version_count,
                    history_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_key,
                    int(entry["current_item_id"]),
                    self._fact_category(fact_key),
                    str(entry["last_updated"]),
                    len(cast(list[int], entry["history"])) + 1,
                    json.dumps(entry["history"], sort_keys=True),
                ),
            )

    def _exact_fact_lookup(self, conn: sqlite3.Connection, *, query: str) -> SearchHit | None:
        """Resolve an exact fact query through the registry before ranked retrieval."""
        fact_key = self._infer_query_fact_key(query)
        if fact_key is None:
            return None
        return self.get_fact(fact_key, conn=conn)

    def _filter_current_fact_hits(
        self,
        conn: sqlite3.Connection,
        hits: Sequence[SearchHit],
    ) -> list[SearchHit]:
        """Filter keyed results down to their current registry representative."""
        rows = conn.execute("SELECT fact_key, current_item_id FROM fact_registry").fetchall()
        current_by_key = {str(row["fact_key"]): int(row["current_item_id"]) for row in rows}
        filtered: list[SearchHit] = []
        for hit in hits:
            if hit.fact_key is None or hit.item_id is None:
                filtered.append(hit)
                continue
            if current_by_key.get(hit.fact_key, hit.item_id) == hit.item_id:
                filtered.append(hit)
        return filtered

    def _search_invalidated_hits(
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        lane: SearchMode,
        scope: str | None,
        limit: int,
    ) -> list[SearchHit]:
        """Return soft-invalidated rows for audit-oriented search calls."""
        like_query = f"%{query.strip().lower()}%"
        lanes = ("memory", "corpus") if lane == "all" else (lane,)
        placeholders = ", ".join("?" for _ in lanes)
        params: list[Any] = [*lanes, like_query, like_query]
        scope_clause = ""
        if scope is not None:
            scope_clause = "AND (scope = ? OR scope = 'global')"
            params.append(scope)
        rows = conn.execute(
            f"""
            SELECT
                id,
                rel_path,
                start_line,
                end_line,
                snippet,
                lane,
                item_type,
                confidence,
                scope,
                evidence_count,
                contradiction_count,
                entities_json,
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
            WHERE lane IN ({placeholders})
              AND invalidated_at IS NOT NULL
              AND (lower(rel_path) LIKE ? OR lower(snippet) LIKE ?)
              {scope_clause}
            ORDER BY modified_at DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            hit = self._row_to_search_hit(row)
            hits.append(replace(hit, score=max(hit.score * 0.25, 0.05)))
        return hits

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

    def _memory_pro_export_rows(
        self, conn: sqlite3.Connection, *, scope: str
    ) -> Iterator[sqlite3.Row]:
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

    def _iter_documents(self) -> Iterator[Any]:
        yield from self._iter_memory_documents()
        yield from self._iter_corpus_documents()

    def _iter_memory_documents(self) -> Iterator[Any]:
        if self.config.include_default_memory:
            for file_name in self.config.memory_file_names:
                path = self.config.workspace_root / file_name
                if path.exists():
                    yield build_document(
                        workspace_root=self.config.workspace_root,
                        path=path,
                        lane="memory",
                        source_name="memory",
                        default_scope=self.config.governance.default_scope,
                    )
        daily_dir = self.config.workspace_root / self.config.daily_dir
        if daily_dir.exists():
            for path in sorted(daily_dir.glob("*.md")):
                yield build_document(
                    workspace_root=self.config.workspace_root,
                    path=path,
                    lane="memory",
                    source_name="daily",
                    default_scope=self.config.governance.default_scope,
                )
        bank_dir = self.config.workspace_root / self.config.bank_dir
        if bank_dir.exists():
            for path in sorted(bank_dir.rglob("*.md")):
                yield build_document(
                    workspace_root=self.config.workspace_root,
                    path=path,
                    lane="memory",
                    source_name="bank",
                    default_scope=self.config.governance.default_scope,
                )

    def _iter_corpus_documents(self) -> Iterator[Any]:
        for source in self.config.corpus_paths:
            if not source.path.exists():
                continue
            if source.path.is_file():
                if matches_glob(source.path.name, source.pattern):
                    yield build_document(
                        workspace_root=self.config.workspace_root,
                        path=source.path,
                        lane="corpus",
                        source_name=source.name,
                        default_scope=self.config.governance.default_scope,
                    )
                continue
            for path in sorted(source.path.rglob("*.md")):
                rel_path = resolve_under_workspace(self.config.workspace_root, path)
                if matches_glob(rel_path, source.pattern):
                    yield build_document(
                        workspace_root=self.config.workspace_root,
                        path=path,
                        lane="corpus",
                        source_name=source.name,
                        default_scope=self.config.governance.default_scope,
                    )

    def _missing_corpus_paths(self) -> list[dict[str, Any]]:
        """Report configured corpus paths that are not currently available."""
        missing: list[dict[str, Any]] = []
        for source in self.config.corpus_paths:
            if source.path.exists():
                continue
            missing.append(
                {
                    "name": source.name,
                    "path": source.path.as_posix(),
                    "pattern": source.pattern,
                    "required": source.required,
                }
            )
        return missing

    def _missing_required_corpus_paths(self) -> list[dict[str, Any]]:
        """Return missing corpus entries that are marked required."""
        return [entry for entry in self._missing_corpus_paths() if bool(entry["required"])]

    def _clear_derived_rows(self, conn: sqlite3.Connection) -> None:
        """Clear the rebuildable tables before a full reindex."""
        for table_name in (
            "backend_state",
            "vector_items",
            "fact_registry",
            "conflicts",
            "evidence_links",
            "proposals",
            "facts",
            "opinions",
            "reflections",
            "entities",
            "search_items_fts",
            "search_items",
            "documents",
        ):
            conn.execute(f"DELETE FROM {table_name}")

    def _insert_typed_row(
        self,
        *,
        conn: sqlite3.Connection,
        item_id: int,
        document_rel_path: str,
        item: Any,
        typed_counts: dict[str, int],
        evidence: list[EvidenceEntry],
    ) -> None:
        """Insert typed rows and evidence/conflict metadata."""
        for evidence_entry in evidence:
            link_key = evidence_entry.link_key()
            if link_key is None:
                continue
            conn.execute(
                """
                INSERT INTO evidence_links(item_id, rel_path, start_line, end_line, relation)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    link_key[0],
                    link_key[1],
                    link_key[2],
                    link_key[3],
                ),
            )
        for target_ref in item.contradicts:
            conn.execute(
                "INSERT INTO conflicts(item_id, target_ref, reason) VALUES (?, ?, ?)",
                (item_id, target_ref, "explicit"),
            )
        if item.item_type == "fact":
            conn.execute(
                "INSERT INTO facts(item_id, rel_path, start_line, end_line, scope, text) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    document_rel_path,
                    item.start_line,
                    item.end_line,
                    item.scope,
                    item.snippet,
                ),
            )
            typed_counts["fact"] += 1
            return
        if item.item_type == "reflection":
            conn.execute(
                "INSERT INTO reflections(item_id, rel_path, start_line, end_line, scope, text) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    document_rel_path,
                    item.start_line,
                    item.end_line,
                    item.scope,
                    item.snippet,
                ),
            )
            typed_counts["reflection"] += 1
            return
        if item.item_type == "opinion":
            conn.execute(
                """
                INSERT INTO opinions(item_id, rel_path, start_line, end_line, scope, text, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    document_rel_path,
                    item.start_line,
                    item.end_line,
                    item.scope,
                    item.snippet,
                    item.confidence,
                ),
            )
            typed_counts["opinion"] += 1
            return
        if item.item_type == "entity":
            entity_name = next(iter(item.entities), item.snippet)
            conn.execute(
                """
                INSERT INTO entities(item_id, rel_path, start_line, end_line, scope, name, text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    document_rel_path,
                    item.start_line,
                    item.end_line,
                    item.scope,
                    entity_name,
                    item.snippet,
                ),
            )
            typed_counts["entity"] += 1
            return
        if item.item_type == "proposal":
            proposal_id = item.proposal_id or self._sha256(item.snippet)
            proposal_kind = self._proposal_kind(item.snippet)
            target = self._store_target(kind=proposal_kind, entity=next(iter(item.entities), None))
            conn.execute(
                """
                INSERT INTO proposals(
                    proposal_id, kind, scope, status, entry_line, source_rel_path, source_line,
                    target_rel_path, entity, confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    proposal_kind,
                    item.scope,
                    item.proposal_status or "pending",
                    item.snippet,
                    document_rel_path,
                    item.start_line,
                    resolve_under_workspace(self.config.workspace_root, target),
                    next(iter(item.entities), None),
                    item.confidence,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
            typed_counts["proposal"] += 1

    def _evidence_entries(
        self,
        rel_path: str,
        start_line: int,
        end_line: int,
        evidence_refs: tuple[str, ...],
    ) -> list[EvidenceEntry]:
        """Build evidence entries, always including the source line itself."""
        entries = [
            EvidenceEntry(
                kind="file",
                rel_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                relation="supports",
            )
        ]
        dedupe_keys = {json.dumps(entries[0].to_dict(), sort_keys=True)}
        for reference in evidence_refs:
            evidence_entry = EvidenceEntry.from_reference(reference, relation="supports")
            dedupe_key = json.dumps(evidence_entry.to_dict(), sort_keys=True)
            if dedupe_key in dedupe_keys:
                continue
            dedupe_keys.add(dedupe_key)
            entries.append(evidence_entry)
        return entries

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
        name = self._slugify(entity or "general")
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
        proposal_id = self._sha256(f"{source_rel_path}:{source_line}:{entry_line}")
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
        return target.item_type  # type: ignore[return-value]

    def _dense_search(
        self,
        *,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[DenseSearchCandidate], float]:
        """Run a dense search query through the configured vector backend."""
        if not self.config.qdrant.enabled or not self.config.embedding.enabled:
            return [], 0.0
        with observed_span(
            "clawops.hypermemory.qdrant.search.dense",
            attributes={
                "lane": lane,
                "scope": scope,
                "candidate_limit": candidate_limit,
            },
        ) as span:
            started_at = perf_counter()
            embedding = self._embed_texts([query.strip()], purpose="query")
            if not embedding:
                return [], 0.0
            try:
                hits = self._qdrant_backend.search_dense(
                    vector=embedding[0],
                    limit=candidate_limit,
                    mode=lane,
                    scope=scope,
                )
            except Exception as err:
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.qdrant.search.dense.error",
                    {"lane": lane, "scope": scope, "error": str(err)},
                )
                raise
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            payload = {
                "lane": lane,
                "scope": scope,
                "hits": len(hits),
                "qdrantSearchMs": elapsed_ms,
            }
            span.set_attributes(payload)
            emit_structured_log("clawops.hypermemory.qdrant.search.dense", payload)
            return hits, elapsed_ms

    def _sparse_search(
        self,
        *,
        conn: sqlite3.Connection,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[SparseSearchCandidate], float]:
        """Run a sparse search query through the configured vector backend."""
        if not self._backend_uses_sparse_vectors():
            return [], 0.0
        encoder = self._load_sparse_encoder(conn)
        if encoder is None:
            raise RuntimeError("sparse encoder state is missing")
        sparse_vector = encoder.encode_query(query.strip())
        if sparse_vector.is_empty:
            return [], 0.0
        with observed_span(
            "clawops.hypermemory.qdrant.search.sparse",
            attributes={
                "lane": lane,
                "scope": scope,
                "candidate_limit": candidate_limit,
            },
        ) as span:
            started_at = perf_counter()
            try:
                hits = self._qdrant_backend.search_sparse(
                    vector=sparse_vector.to_qdrant(),
                    limit=candidate_limit,
                    mode=lane,
                    scope=scope,
                )
            except Exception as err:
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.qdrant.search.sparse.error",
                    {"lane": lane, "scope": scope, "error": str(err)},
                )
                raise
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            payload = {
                "lane": lane,
                "scope": scope,
                "hits": len(hits),
                "qdrantSearchMs": elapsed_ms,
            }
            span.set_attributes(payload)
            emit_structured_log("clawops.hypermemory.qdrant.search.sparse", payload)
            return hits, elapsed_ms

    def _sync_dense_backend(
        self,
        *,
        conn: sqlite3.Connection,
        vector_rows: list[dict[str, Any]],
        stale_point_ids: set[str],
        sparse_encoder: SparseEncoder,
    ) -> None:
        """Synchronize dense and sparse vectors into Qdrant when the backend is enabled."""
        conn.execute("DELETE FROM backend_state")
        conn.execute("DELETE FROM sparse_terms")
        self._write_sparse_state(conn, sparse_encoder)
        if not self.config.qdrant.enabled or not self.config.embedding.enabled:
            conn.execute("DELETE FROM vector_items")
            self._write_backend_state(conn, "config_fingerprint", self._backend_fingerprint())
            self._write_backend_state(conn, "last_sync_at", datetime.now(tz=UTC).isoformat())
            self._write_backend_state(
                conn,
                "last_sync_error",
                "qdrant backend disabled" if self._backend_uses_qdrant() else "",
            )
            conn.commit()
            emit_structured_log(
                "clawops.hypermemory.vector_sync",
                {"skipped": True, "reason": "disabled", "vectorRows": len(vector_rows)},
            )
            return
        if not vector_rows:
            conn.execute("DELETE FROM vector_items")
            self._write_backend_state(conn, "config_fingerprint", self._backend_fingerprint())
            self._write_backend_state(conn, "last_sync_at", datetime.now(tz=UTC).isoformat())
            self._write_backend_state(conn, "last_sync_error", "")
            conn.commit()
            emit_structured_log(
                "clawops.hypermemory.vector_sync",
                {"skipped": True, "reason": "empty", "vectorRows": 0},
            )
            return
        with observed_span(
            "clawops.hypermemory.vector_sync",
            attributes={"vector_rows": len(vector_rows), "stale_point_ids": len(stale_point_ids)},
        ) as span:
            sync_started_at = perf_counter()
            try:
                include_sparse = self._backend_uses_sparse_vectors()
                points: list[dict[str, Any]] = []
                embedded_vectors: list[
                    tuple[dict[str, Any], list[float], dict[str, Any] | None]
                ] = []
                for batch in self._embedding_batches(vector_rows):
                    vectors = self._embed_texts(
                        [str(entry["content"]) for entry in batch],
                        purpose="index",
                    )
                    for entry, vector in zip(batch, vectors, strict=True):
                        sparse_payload = None
                        if include_sparse:
                            sparse_vector = sparse_encoder.encode_document(str(entry["content"]))
                            sparse_payload = sparse_vector.to_qdrant()
                            entry["sparse_term_count"] = len(sparse_vector.indices)
                        else:
                            entry["sparse_term_count"] = 0
                        embedded_vectors.append((entry, vector, sparse_payload))
                vector_dim = len(embedded_vectors[0][1])
                self._qdrant_backend.ensure_collection(
                    vector_size=vector_dim,
                    include_sparse=include_sparse,
                )
                new_point_ids: set[str] = set()
                for entry, vector, sparse_payload in embedded_vectors:
                    point_id = str(entry["point_id"])
                    new_point_ids.add(point_id)
                    vector_payload: dict[str, Any] = {
                        self.config.qdrant.dense_vector_name: vector,
                    }
                    if include_sparse and sparse_payload is not None:
                        vector_payload[self.config.qdrant.sparse_vector_name] = sparse_payload
                    points.append(
                        {
                            "id": point_id,
                            "vector": vector_payload,
                            "payload": entry["payload"],
                        }
                    )
                self._qdrant_backend.upsert_points(points)
                stale_ids = sorted(stale_point_ids - new_point_ids)
                if stale_ids:
                    self._qdrant_backend.delete_points(stale_ids)
                conn.execute("DELETE FROM vector_items")
                for entry, vector, _sparse_payload in embedded_vectors:
                    conn.execute(
                        """
                        INSERT INTO vector_items(
                            item_id,
                            point_id,
                            embedding_model,
                            embedding_dim,
                            content_sha256,
                            sparse_term_count,
                            sparse_content_sha256,
                            sparse_updated_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(entry["item_id"]),
                            str(entry["point_id"]),
                            self.config.embedding.model,
                            len(vector),
                            self._sha256(str(entry["content"])),
                            int(entry.get("sparse_term_count", 0)),
                            sparse_encoder.fingerprint if include_sparse else "",
                            datetime.now(tz=UTC).isoformat() if include_sparse else "",
                            datetime.now(tz=UTC).isoformat(),
                        ),
                    )
                self._write_backend_state(conn, "config_fingerprint", self._backend_fingerprint())
                self._write_backend_state(conn, "last_sync_at", datetime.now(tz=UTC).isoformat())
                self._write_backend_state(conn, "last_sync_error", "")
                conn.commit()
                payload = {
                    "vectorRows": len(vector_rows),
                    "pointsUpserted": len(points),
                    "pointsDeleted": len(stale_ids),
                    "vectorSyncMs": round((perf_counter() - sync_started_at) * 1000.0, 3),
                }
                span.set_attributes(payload)
                emit_structured_log("clawops.hypermemory.vector_sync", payload)
            except Exception as err:
                self._write_backend_state(conn, "last_sync_error", str(err))
                conn.commit()
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.vector_sync.error",
                    {"vectorRows": len(vector_rows), "error": str(err)},
                )
                raise

    def _embed_texts(self, texts: Sequence[str], *, purpose: str) -> list[list[float]]:
        """Call the embedding provider while emitting structured telemetry."""
        with observed_span(
            "clawops.hypermemory.embedding",
            attributes={
                "purpose": purpose,
                "batch_size": len(texts),
                "provider": self.config.embedding.provider,
                "model": self.config.embedding.model,
            },
        ) as span:
            started_at = perf_counter()
            try:
                vectors = self._embedding_provider.embed_texts(list(texts))
            except Exception as err:
                elapsed_ms = (perf_counter() - started_at) * 1000.0
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.embedding.failure",
                    {
                        "purpose": purpose,
                        "batchSize": len(texts),
                        "embeddingMs": round(elapsed_ms, 3),
                        "error": str(err),
                    },
                )
                raise
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            payload: dict[str, bool | int | float | str | None] = {
                "purpose": purpose,
                "batchSize": len(texts),
                "vectorCount": len(vectors),
                "embeddingMs": round(elapsed_ms, 3),
            }
            span.set_attributes(payload)
            emit_structured_log("clawops.hypermemory.embedding", payload)
            return vectors

    def _embedding_batches(
        self, vector_rows: list[dict[str, Any]]
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield embedding work in bounded batches."""
        batch_size = max(self.config.embedding.batch_size, 1)
        for index in range(0, len(vector_rows), batch_size):
            yield vector_rows[index : index + batch_size]

    def _count_sparse_vector_items(self, conn: sqlite3.Connection) -> int:
        """Count indexed rows that carry sparse vector state."""
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM vector_items WHERE sparse_term_count > 0"
        ).fetchone()
        return 0 if row is None else int(row["count"])

    def _canonical_backend(self, backend: SearchBackend) -> SearchBackend:
        """Return the backend identifier unchanged."""
        return backend

    def _backend_uses_qdrant(self) -> bool:
        """Return whether the configured active backend uses Qdrant."""
        return self.config.backend.active in {
            "qdrant_dense_hybrid",
            "qdrant_sparse_dense_hybrid",
        }

    def _backend_uses_sparse_vectors(self) -> bool:
        """Return whether the configured active backend expects sparse vectors."""
        return self.config.backend.active == "qdrant_sparse_dense_hybrid"

    def _vector_rows_for_documents(
        self, documents: Sequence[IndexedDocument]
    ) -> list[dict[str, str]]:
        """Build deterministic retrieval rows from indexed documents."""
        vector_rows: list[dict[str, str]] = []
        for document in documents:
            for item in document.items:
                vector_rows.append(
                    {
                        "content": self._normalized_retrieval_text(item.title, item.snippet),
                    }
                )
        return vector_rows

    def _sparse_encoder_for_documents(self, documents: Sequence[IndexedDocument]) -> SparseEncoder:
        """Build the sparse encoder for the current indexed corpus."""
        if not self._backend_uses_sparse_vectors():
            return build_sparse_encoder(())
        return build_sparse_encoder(
            [str(entry["content"]) for entry in self._vector_rows_for_documents(documents)]
        )

    def _sparse_fingerprint_for_documents(self, documents: Sequence[IndexedDocument]) -> str:
        """Return the sparse fingerprint for *documents*."""
        return self._sparse_encoder_for_documents(documents).fingerprint

    def _current_sparse_fingerprint(self) -> str:
        """Return the sparse fingerprint for the current canonical corpus."""
        return self._sparse_fingerprint_for_documents(list(self._iter_documents()))

    def _write_sparse_state(self, conn: sqlite3.Connection, sparse_encoder: SparseEncoder) -> None:
        """Persist sparse vocabulary metadata into the derived SQLite state."""
        if not self._backend_uses_sparse_vectors():
            self._write_backend_state(conn, "sparse_fingerprint", "")
            self._write_backend_state(conn, "sparse_doc_count", "0")
            self._write_backend_state(conn, "sparse_avg_doc_length", "0")
            return
        for term, term_id in sorted(sparse_encoder.term_to_id.items(), key=lambda item: item[1]):
            conn.execute(
                "INSERT INTO sparse_terms(term, term_id, document_freq) VALUES (?, ?, ?)",
                (term, term_id, int(sparse_encoder.document_frequency.get(term, 0))),
            )
        self._write_backend_state(conn, "sparse_fingerprint", sparse_encoder.fingerprint)
        self._write_backend_state(
            conn,
            "sparse_doc_count",
            str(sparse_encoder.document_count),
        )
        self._write_backend_state(
            conn,
            "sparse_avg_doc_length",
            f"{sparse_encoder.average_document_length:.8f}",
        )

    def _load_sparse_encoder(self, conn: sqlite3.Connection) -> SparseEncoder | None:
        """Load the persisted sparse vocabulary from SQLite."""
        if not self._backend_uses_sparse_vectors():
            return None
        rows = conn.execute(
            "SELECT term, term_id, document_freq FROM sparse_terms ORDER BY term_id ASC"
        ).fetchall()
        if not rows:
            return None
        term_to_id = {str(row["term"]): int(row["term_id"]) for row in rows}
        document_frequency = {str(row["term"]): int(row["document_freq"]) for row in rows}
        document_count = int(self._backend_state_value(conn, "sparse_doc_count") or "0")
        average_document_length = float(
            self._backend_state_value(conn, "sparse_avg_doc_length") or "0"
        )
        fingerprint = self._backend_state_value(conn, "sparse_fingerprint") or ""
        return SparseEncoder(
            term_to_id=term_to_id,
            document_frequency=document_frequency,
            document_count=document_count,
            average_document_length=average_document_length,
            fingerprint=fingerprint,
        )

    def _collection_has_hypermemory_vector_lanes(self, collection_details: dict[str, Any]) -> bool:
        """Return whether the live Qdrant collection exposes both named vector lanes."""
        config = collection_details.get("config")
        if not isinstance(config, dict):
            return False
        params = config.get("params")
        if not isinstance(params, dict):
            return False
        vectors = params.get("vectors")
        sparse_vectors = params.get("sparse_vectors")
        if not isinstance(vectors, dict) or not isinstance(sparse_vectors, dict):
            return False
        return (
            self.config.qdrant.dense_vector_name in vectors
            and self.config.qdrant.sparse_vector_name in sparse_vectors
        )

    def _hypermemory_probe_query(self, conn: sqlite3.Connection) -> str | None:
        """Return a deterministic probe query for hypermemory verification."""
        row = conn.execute("""
            SELECT normalized_text
            FROM search_items
            WHERE normalized_text != ''
            ORDER BY length(normalized_text) DESC
            LIMIT 1
            """).fetchone()
        if row is None:
            return None
        text = str(row["normalized_text"]).strip()
        if not text:
            return None
        return " ".join(text.split()[:8])

    def _backend_fingerprint(self) -> str:
        """Return a stable fingerprint for the active dense backend config."""
        payload = {
            "active": self.config.backend.active,
            "fallback": self.config.backend.fallback,
            "embedding": {
                "enabled": self.config.embedding.enabled,
                "provider": self.config.embedding.provider,
                "model": self.config.embedding.model,
                "base_url": self.config.embedding.base_url,
                "dimensions": self.config.embedding.dimensions,
            },
            "qdrant": {
                "enabled": self.config.qdrant.enabled,
                "url": self.config.qdrant.url,
                "collection": self.config.qdrant.collection,
                "dense_vector_name": self.config.qdrant.dense_vector_name,
                "sparse_vector_name": self.config.qdrant.sparse_vector_name,
            },
        }
        return self._sha256(json.dumps(payload, sort_keys=True))

    def _backend_state_value(self, conn: sqlite3.Connection, key: str) -> str | None:
        """Return the current backend state value for *key*."""
        row = conn.execute("SELECT value FROM backend_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def _write_backend_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        """Persist a backend state value."""
        conn.execute(
            "INSERT OR REPLACE INTO backend_state(key, value) VALUES (?, ?)",
            (key, value),
        )

    def _count_rows(self, conn: sqlite3.Connection, table_name: str) -> int:
        """Count rows inside *table_name*."""
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return 0 if row is None else int(row["count"])

    def _normalized_retrieval_text(self, title: str, snippet: str) -> str:
        """Return normalized text used for lexical and dense retrieval."""
        combined = f"{title} {snippet}"
        return " ".join(token for token in self._normalize_text(combined))

    def _normalize_text(self, text: str) -> tuple[str, ...]:
        """Normalize text into lowercase search tokens."""
        collapsed = "".join(character.lower() if character.isalnum() else " " for character in text)
        return tuple(token for token in collapsed.split() if token)

    def _point_id(
        self,
        *,
        document_rel_path: str,
        item_type: str,
        start_line: int,
        end_line: int,
        snippet: str,
    ) -> str:
        """Return a stable point identifier for a search item."""
        digest = self._sha256(
            f"{document_rel_path}:{item_type}:{start_line}:{end_line}:{snippet.strip()}"
        )[:32]
        return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"

    def _slugify(self, value: str) -> str:
        """Return a stable slug for an entity file path."""
        lowered = value.strip().lower()
        slug = "".join(character if character.isalnum() else "-" for character in lowered)
        collapsed = "-".join(part for part in slug.split("-") if part)
        return collapsed or "entity"

    def _sha256(self, value: str) -> str:
        """Return a SHA-256 hex digest for *value*."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
