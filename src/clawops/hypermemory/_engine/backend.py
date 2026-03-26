"""Backend integration methods for the StrongClaw hypermemory engine."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
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
from clawops.hypermemory.sparse import SparseEncoder
from clawops.hypermemory.utils import sha256
from clawops.observability import emit_structured_log, observed_span


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
            embedded_vectors: list[tuple[dict[str, Any], list[float], dict[str, Any] | None]] = []
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
                        sha256(str(entry["content"])),
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


def _embedding_batches(self, vector_rows: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    """Yield embedding work in bounded batches."""
    batch_size = max(self.config.embedding.batch_size, 1)
    for index in range(0, len(vector_rows), batch_size):
        yield vector_rows[index : index + batch_size]


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


def _vector_rows_for_documents(self, documents: Sequence[IndexedDocument]) -> list[dict[str, str]]:
    """Build deterministic retrieval rows from indexed documents."""
    return self.backend.vector_rows_for_documents(documents)


def _sparse_encoder_for_documents(self, documents: Sequence[IndexedDocument]) -> SparseEncoder:
    """Build the sparse encoder for the current indexed corpus."""
    return self.backend.sparse_encoder_for_documents(documents)


def _sparse_fingerprint_for_documents(self, documents: Sequence[IndexedDocument]) -> str:
    """Return the sparse fingerprint for *documents*."""
    return self.backend.sparse_fingerprint_for_documents(documents)


def _current_sparse_fingerprint(self) -> str:
    """Return the sparse fingerprint for the current canonical corpus."""
    return self.backend.sparse_fingerprint_for_documents(list(self._iter_documents()))


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
    average_document_length = float(self._backend_state_value(conn, "sparse_avg_doc_length") or "0")
    fingerprint = self._backend_state_value(conn, "sparse_fingerprint") or ""
    return SparseEncoder(
        term_to_id=term_to_id,
        document_frequency=document_frequency,
        document_count=document_count,
        average_document_length=average_document_length,
        fingerprint=fingerprint,
    )


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
    return sha256(json.dumps(payload, sort_keys=True))


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
