"""Query and verification methods for the StrongClaw hypermemory engine."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import replace
from time import perf_counter
from typing import Any, Callable, Sequence, cast

from clawops.hypermemory.models import (
    DenseSearchCandidate,
    FusionMode,
    Lane,
    RerankResponse,
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
from clawops.observability import TelemetryValue, emit_structured_log, observed_span


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
                errors.append("Qdrant collection is missing the named dense or sparse vector lane")

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
        bad_recall_count=(int(row["bad_recall_count"]) if "bad_recall_count" in row_keys else 0),
        fact_key=(
            None if "fact_key" not in row_keys or row["fact_key"] is None else str(row["fact_key"])
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
