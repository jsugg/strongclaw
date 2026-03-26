"""Vector backend integration service.

This service owns interactions with the embedding provider and vector backend.
It may persist backend-state via IndexService, but it must not depend on the
derived-index build logic (to keep the dependency graph acyclic).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from clawops.hypermemory.models import (
    DenseSearchCandidate,
    IndexedDocument,
    SearchBackend,
    SearchMode,
    SparseSearchCandidate,
)
from clawops.hypermemory.providers import EmbeddingProvider
from clawops.hypermemory.qdrant_backend import VectorBackend
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.sparse import SparseEncoder, build_sparse_encoder
from clawops.hypermemory.utils import normalized_retrieval_text, sha256
from clawops.observability import emit_structured_log, observed_span


class BackendService:
    """Stateful vector-backend integration surface."""

    def __init__(
        self,
        *,
        config: Any,
        connect: Callable[[], sqlite3.Connection],
        embedding_provider: EmbeddingProvider,
        vector_backend: VectorBackend,
        index: IndexService,
    ) -> None:
        self._config = config
        self._connect = connect
        self._embedding_provider = embedding_provider
        self._vector_backend = vector_backend
        self._index = index

    @property
    def config(self) -> Any:
        return self._config

    def backend_uses_qdrant(self) -> bool:
        return self._config.backend.active in {"qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"}

    def backend_uses_sparse_vectors(self) -> bool:
        return self._config.backend.active == "qdrant_sparse_dense_hybrid"

    def backend_fingerprint(self) -> str:
        payload = {
            "active": self._config.backend.active,
            "fallback": self._config.backend.fallback,
            "embedding": {
                "enabled": self._config.embedding.enabled,
                "provider": self._config.embedding.provider,
                "model": self._config.embedding.model,
                "base_url": self._config.embedding.base_url,
                "dimensions": self._config.embedding.dimensions,
            },
            "qdrant": {
                "enabled": self._config.qdrant.enabled,
                "url": self._config.qdrant.url,
                "collection": self._config.qdrant.collection,
                "dense_vector_name": self._config.qdrant.dense_vector_name,
                "sparse_vector_name": self._config.qdrant.sparse_vector_name,
            },
        }
        return sha256(json.dumps(payload, sort_keys=True))

    def vector_rows_for_documents(
        self, documents: Sequence[IndexedDocument]
    ) -> list[dict[str, str]]:
        """Build deterministic retrieval rows from indexed documents."""
        vector_rows: list[dict[str, str]] = []
        for document in documents:
            for item in document.items:
                vector_rows.append(
                    {
                        "content": normalized_retrieval_text(item.title, item.snippet),
                    }
                )
        return vector_rows

    def sparse_encoder_for_documents(self, documents: Sequence[IndexedDocument]) -> SparseEncoder:
        """Build the sparse encoder for the current indexed corpus."""
        if not self.backend_uses_sparse_vectors():
            return build_sparse_encoder(())
        return build_sparse_encoder(
            [str(entry["content"]) for entry in self.vector_rows_for_documents(documents)]
        )

    def sparse_fingerprint_for_documents(self, documents: Sequence[IndexedDocument]) -> str:
        """Return the sparse fingerprint for *documents*."""
        return self.sparse_encoder_for_documents(documents).fingerprint

    def embedding_batches(
        self, vector_rows: list[dict[str, Any]]
    ) -> Iterator[list[dict[str, Any]]]:
        batch_size = max(int(self._config.embedding.batch_size), 1)
        for index in range(0, len(vector_rows), batch_size):
            yield vector_rows[index : index + batch_size]

    def embed_texts(self, texts: Sequence[str], *, purpose: str) -> list[list[float]]:
        with observed_span(
            "clawops.hypermemory.embedding",
            attributes={
                "purpose": purpose,
                "batch_size": len(texts),
                "provider": self._config.embedding.provider,
                "model": self._config.embedding.model,
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

    def dense_search(
        self,
        *,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[DenseSearchCandidate], float]:
        if not self._config.qdrant.enabled or not self._config.embedding.enabled:
            return [], 0.0
        with observed_span(
            "clawops.hypermemory.qdrant.search.dense",
            attributes={"lane": lane, "scope": scope, "candidate_limit": candidate_limit},
        ) as span:
            started_at = perf_counter()
            embedding = self.embed_texts([query.strip()], purpose="query")
            if not embedding:
                return [], 0.0
            try:
                hits = self._vector_backend.search_dense(
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

    def sparse_search(
        self,
        *,
        conn: sqlite3.Connection,
        query: str,
        lane: SearchMode,
        scope: str | None,
        candidate_limit: int,
    ) -> tuple[list[SparseSearchCandidate], float]:
        if not self.backend_uses_sparse_vectors():
            return [], 0.0
        encoder = self._index.load_sparse_encoder(conn, enabled=True)
        if encoder is None:
            raise RuntimeError("sparse encoder state is missing")
        sparse_vector = encoder.encode_query(query.strip())
        if sparse_vector.is_empty:
            return [], 0.0
        with observed_span(
            "clawops.hypermemory.qdrant.search.sparse",
            attributes={"lane": lane, "scope": scope, "candidate_limit": candidate_limit},
        ) as span:
            started_at = perf_counter()
            try:
                hits = self._vector_backend.search_sparse(
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

    def sync_vectors(
        self,
        *,
        conn: sqlite3.Connection,
        vector_rows: list[dict[str, Any]],
        stale_point_ids: set[str],
        sparse_encoder: SparseEncoder,
    ) -> None:
        conn.execute("DELETE FROM backend_state")
        conn.execute("DELETE FROM sparse_terms")
        self._index.write_sparse_state(
            conn, sparse_encoder, enabled=self.backend_uses_sparse_vectors()
        )
        if not self._config.qdrant.enabled or not self._config.embedding.enabled:
            conn.execute("DELETE FROM vector_items")
            self._index.write_backend_state(conn, "config_fingerprint", self.backend_fingerprint())
            self._index.write_backend_state(conn, "last_sync_at", datetime.now(tz=UTC).isoformat())
            self._index.write_backend_state(
                conn,
                "last_sync_error",
                "qdrant backend disabled" if self.backend_uses_qdrant() else "",
            )
            conn.commit()
            emit_structured_log(
                "clawops.hypermemory.vector_sync",
                {"skipped": True, "reason": "disabled", "vectorRows": len(vector_rows)},
            )
            return
        if not vector_rows:
            conn.execute("DELETE FROM vector_items")
            self._index.write_backend_state(conn, "config_fingerprint", self.backend_fingerprint())
            self._index.write_backend_state(conn, "last_sync_at", datetime.now(tz=UTC).isoformat())
            self._index.write_backend_state(conn, "last_sync_error", "")
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
                include_sparse = self.backend_uses_sparse_vectors()
                points: list[dict[str, Any]] = []
                embedded_vectors: list[
                    tuple[dict[str, Any], list[float], dict[str, Any] | None]
                ] = []
                for batch in self.embedding_batches(vector_rows):
                    vectors = self.embed_texts(
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
                self._vector_backend.ensure_collection(
                    vector_size=vector_dim, include_sparse=include_sparse
                )
                new_point_ids: set[str] = set()
                for entry, vector, sparse_payload in embedded_vectors:
                    point_id = str(entry["point_id"])
                    new_point_ids.add(point_id)
                    vector_payload: dict[str, Any] = {
                        self._config.qdrant.dense_vector_name: vector,
                    }
                    if include_sparse and sparse_payload is not None:
                        vector_payload[self._config.qdrant.sparse_vector_name] = sparse_payload
                    points.append(
                        {"id": point_id, "vector": vector_payload, "payload": entry["payload"]}
                    )
                self._vector_backend.upsert_points(points)
                stale_ids = sorted(stale_point_ids - new_point_ids)
                if stale_ids:
                    self._vector_backend.delete_points(stale_ids)
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
                            self._config.embedding.model,
                            len(vector),
                            sha256(str(entry["content"])),
                            int(entry.get("sparse_term_count", 0)),
                            sparse_encoder.fingerprint if include_sparse else "",
                            datetime.now(tz=UTC).isoformat() if include_sparse else "",
                            datetime.now(tz=UTC).isoformat(),
                        ),
                    )
                self._index.write_backend_state(
                    conn, "config_fingerprint", self.backend_fingerprint()
                )
                self._index.write_backend_state(
                    conn, "last_sync_at", datetime.now(tz=UTC).isoformat()
                )
                self._index.write_backend_state(conn, "last_sync_error", "")
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
                self._index.write_backend_state(conn, "last_sync_error", str(err))
                conn.commit()
                span.record_exception(err)
                span.set_error(str(err))
                emit_structured_log(
                    "clawops.hypermemory.vector_sync.error",
                    {"vectorRows": len(vector_rows), "error": str(err)},
                )
                raise

    def canonical_backend(self, backend: SearchBackend) -> SearchBackend:
        return backend
