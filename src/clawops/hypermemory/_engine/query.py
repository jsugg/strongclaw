"""Query methods for the StrongClaw hypermemory engine."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any, Sequence, cast

from clawops.hypermemory.models import (
    DenseSearchCandidate,
    FusionMode,
    SearchBackend,
    SearchHit,
    SearchMode,
    SparseSearchCandidate,
)
from clawops.hypermemory.retrieval import (
    _adaptive_pool_size,
    _count_active_items,
    _count_items,
    _estimate_query_specificity,
    _invalidated_ratio,
    search_index,
)
from clawops.hypermemory.schema import SCHEMA_VERSION
from clawops.observability import emit_structured_log, observed_span


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
        "rerankFallbackModel": self.config.rerank.model_for(self.config.rerank.fallback_provider),
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
    return current != existing or backend_fingerprint != self._backend_fingerprint() or sparse_dirty


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
