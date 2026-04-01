"""Verification service for the StrongClaw hypermemory engine."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import replace
from time import perf_counter

from clawops.hypermemory.contracts import VerificationDeps, VerifyLaneChecks, VerifyResult
from clawops.hypermemory.models import HypermemoryConfig, RerankResponse
from clawops.hypermemory.providers import RerankProvider
from clawops.hypermemory.qdrant_backend import VectorBackend
from clawops.hypermemory.services.backend_service import BackendService
from clawops.observability import TelemetryValue, emit_structured_log, observed_span
from clawops.typed_values import as_optional_mapping


class VerificationService:
    """Own verification and rerank observability behavior for hypermemory."""

    def __init__(
        self,
        *,
        config: HypermemoryConfig,
        connect: Callable[[], sqlite3.Connection],
        backend: BackendService,
        vector_backend: VectorBackend,
        rerank_provider: RerankProvider,
        deps: VerificationDeps,
    ) -> None:
        self._config = config
        self._connect = connect
        self._backend = backend
        self._vector_backend = vector_backend
        self._rerank_provider = rerank_provider
        self._deps = deps

    def observed_rerank_scorer(self, query: str, documents: Sequence[str]) -> RerankResponse:
        """Return telemetry-wrapped rerank scores."""
        if not documents:
            return RerankResponse()
        with observed_span(
            "clawops.hypermemory.rerank",
            attributes={
                "configuredProvider": self._config.rerank.provider,
                "fallbackProvider": self._config.rerank.fallback_provider,
                "configuredDevice": self._config.rerank.local.device,
                "resolvedDevice": self.rerank_resolved_device(),
                "candidateCount": len(documents),
            },
        ) as span:
            started_at = perf_counter()
            try:
                response = self._rerank_provider.score(query, documents)
            except Exception as err:
                latency_ms = (perf_counter() - started_at) * 1000.0
                error_payload: dict[str, TelemetryValue] = {
                    "configuredProvider": self._config.rerank.provider,
                    "fallbackProvider": self._config.rerank.fallback_provider,
                    "configuredDevice": self._config.rerank.local.device,
                    "resolvedDevice": self.rerank_resolved_device(),
                    "candidateCount": len(documents),
                    "rerankMs": round(latency_ms, 3),
                    "error": str(err),
                }
                span.record_exception(err)
                span.set_error(str(err))
                span.set_attributes(error_payload)
                emit_structured_log("clawops.hypermemory.rerank.error", error_payload)
                if not self._config.rerank.fail_open:
                    raise
                return RerankResponse(
                    latency_ms=latency_ms,
                    fail_open=True,
                    error=str(err),
                )
            latency_ms = (perf_counter() - started_at) * 1000.0
            observed_response = replace(response, latency_ms=latency_ms)
            payload: dict[str, TelemetryValue] = {
                "configuredProvider": self._config.rerank.provider,
                "provider": observed_response.provider,
                "fallbackProvider": self._config.rerank.fallback_provider,
                "configuredDevice": self._config.rerank.local.device,
                "resolvedDevice": self.rerank_resolved_device(),
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

    def rerank_resolved_device(self) -> str:
        """Return the current runtime device selected for reranking."""
        resolver = getattr(self._rerank_provider, "resolved_device", None)
        if not callable(resolver):
            return ""
        try:
            return str(resolver())
        except Exception:
            return ""

    def verify(self) -> VerifyResult:
        """Verify the supported sparse+dense backend contract for hypermemory."""
        errors: list[str] = []
        lane_checks: VerifyLaneChecks = {
            "probeQuery": "",
            "rerank": {
                "required": (
                    self._config.rerank.enabled
                    and self._config.hybrid.rerank_candidate_pool > 0
                    and not self._config.rerank.fail_open
                ),
                "candidatePool": self._config.hybrid.rerank_candidate_pool,
            },
        }
        if self._config.backend.active != "qdrant_sparse_dense_hybrid":
            errors.append(
                "backend.active must be qdrant_sparse_dense_hybrid for hypermemory verification"
            )
        status = self._deps.status()
        missing_required_paths = self._deps.missing_required_corpus_paths()
        if missing_required_paths:
            names = ", ".join(entry["name"] for entry in missing_required_paths)
            errors.append(f"required corpus paths are missing: {names}")
        if status["dirty"]:
            errors.append("hypermemory index is dirty")
        if status["lastVectorSyncError"]:
            if status["vectorSyncDeferred"] and status["searchItems"] > 0:
                errors.append(f"vector sync deferred: {status['lastVectorSyncError']}")
            else:
                errors.append(f"vector sync error: {status['lastVectorSyncError']}")
        if not status["qdrantEnabled"] or not status["qdrantHealthy"]:
            errors.append("Qdrant must be enabled and healthy")
        if status["vectorItems"] <= 0:
            errors.append("no dense vector items are indexed")
        if status["sparseVectorItems"] <= 0:
            errors.append("no sparse vector items are indexed")
        if status["sparseFingerprintDirty"]:
            errors.append("sparse fingerprint is dirty")

        collection_details: dict[str, object] = {}
        if status["qdrantEnabled"] and status["qdrantHealthy"]:
            try:
                collection_details = dict(self._vector_backend.collection_details())
            except Exception as err:
                errors.append(f"unable to read Qdrant collection details: {err}")
            else:
                if not self._collection_has_hypermemory_vector_lanes(collection_details):
                    errors.append(
                        "Qdrant collection is missing the named dense or sparse vector lane"
                    )

        with self._connect() as conn:
            probe_query = self._hypermemory_probe_query(conn)
            lane_checks["probeQuery"] = probe_query or ""
            if not probe_query:
                errors.append("unable to build a hypermemory probe query from indexed content")
            else:
                try:
                    dense_hits, dense_ms = self._backend.dense_search(
                        query=probe_query,
                        lane="all",
                        scope=None,
                        candidate_limit=self._config.hybrid.dense_candidate_pool,
                    )
                    lane_checks["dense"] = {"hits": len(dense_hits), "ms": round(dense_ms, 3)}
                    if not dense_hits:
                        errors.append("dense lane returned no candidates")
                except Exception as err:
                    errors.append(f"dense lane failed: {err}")
                try:
                    sparse_hits, sparse_ms = self._backend.sparse_search(
                        conn=conn,
                        query=probe_query,
                        lane="all",
                        scope=None,
                        candidate_limit=self._config.hybrid.sparse_candidate_pool,
                    )
                    lane_checks["sparse"] = {"hits": len(sparse_hits), "ms": round(sparse_ms, 3)}
                    if not sparse_hits:
                        errors.append("sparse lane returned no candidates")
                except Exception as err:
                    errors.append(f"sparse lane failed: {err}")
                rerank_required = lane_checks["rerank"]["required"]
                if rerank_required:
                    probe_documents = self._rerank_probe_documents(
                        conn,
                        limit=min(self._config.hybrid.rerank_candidate_pool, 2),
                    )
                    lane_checks["rerank"]["documents"] = len(probe_documents)
                    if not probe_documents:
                        errors.append("unable to build rerank probe documents from indexed content")
                    else:
                        try:
                            provider, fallback_used, candidate_count, rerank_ms = (
                                self._verify_rerank_provider(
                                    query=probe_query,
                                    documents=probe_documents,
                                )
                            )
                            lane_checks["rerank"]["provider"] = provider
                            lane_checks["rerank"]["fallbackUsed"] = fallback_used
                            lane_checks["rerank"]["candidateCount"] = candidate_count
                            lane_checks["rerank"]["rerankMs"] = rerank_ms
                        except Exception as err:
                            errors.append(f"rerank provider failed: {err}")

        return {
            "ok": not errors,
            "provider": "strongclaw-hypermemory",
            "backend": self._config.backend.active,
            "status": status,
            "collection": collection_details,
            "laneChecks": lane_checks,
            "errors": errors,
        }

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
    ) -> tuple[str, bool, int, float]:
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
        return (
            response.provider,
            response.fallback_used,
            len(documents),
            round(latency_ms, 3),
        )

    def _collection_has_hypermemory_vector_lanes(
        self, collection_details: dict[str, object]
    ) -> bool:
        """Return whether the live Qdrant collection exposes both named vector lanes."""
        config = as_optional_mapping(
            collection_details.get("config"),
            path="collection_details.config",
        )
        if config is None:
            return False
        params = as_optional_mapping(config.get("params"), path="collection_details.config.params")
        if params is None:
            return False
        vectors = as_optional_mapping(
            params.get("vectors"),
            path="collection_details.config.params.vectors",
        )
        sparse_vectors = as_optional_mapping(
            params.get("sparse_vectors"),
            path="collection_details.config.params.sparse_vectors",
        )
        if vectors is None or sparse_vectors is None:
            return False
        return (
            self._config.qdrant.dense_vector_name in vectors
            and self._config.qdrant.sparse_vector_name in sparse_vectors
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
