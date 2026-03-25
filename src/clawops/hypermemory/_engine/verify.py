"""Verification helpers for the StrongClaw hypermemory engine."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import replace
from time import perf_counter
from typing import Any, Callable, Sequence, cast

from clawops.hypermemory.models import RerankResponse
from clawops.observability import TelemetryValue, emit_structured_log, observed_span


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
