"""Core engine for StrongClaw hypermemory."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, cast

from clawops.common import ensure_parent
from clawops.hypermemory.config import HypermemoryConfig, matches_glob, resolve_under_workspace
from clawops.hypermemory.governance import ensure_writable_scope, should_auto_apply, validate_scope
from clawops.hypermemory.models import (
    DenseSearchCandidate,
    EvidenceEntry,
    FusionMode,
    IndexedDocument,
    ProposalRecord,
    ReflectionMode,
    ReflectionSummary,
    ReindexSummary,
    SearchBackend,
    SearchHit,
    SearchMode,
    SparseSearchCandidate,
)
from clawops.hypermemory.parser import build_document, iter_retained_notes, parse_typed_entry
from clawops.hypermemory.providers import create_embedding_provider, create_rerank_provider
from clawops.hypermemory.qdrant_backend import QdrantBackend
from clawops.hypermemory.retrieval import search_index
from clawops.hypermemory.schema import SCHEMA_VERSION, ensure_schema
from clawops.hypermemory.sparse import SparseEncoder, build_sparse_encoder
from clawops.observability import emit_structured_log, observed_span

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
            "embeddingEnabled": self.config.embedding.enabled,
            "embeddingProvider": self.config.embedding.provider,
            "embeddingModel": self.config.embedding.model,
            "rerankEnabled": self.config.rerank.enabled,
            "rerankProvider": self.config.rerank.provider,
            "rerankModel": self.config.rerank.model,
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

    def reindex(self) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
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
                                    evidence_json
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    if resolved_backend in {
                        "qdrant_dense_hybrid",
                        "qdrant_sparse_dense_hybrid",
                    }:
                        try:
                            dense_candidates, qdrant_dense_search_ms = self._dense_search(
                                query=query,
                                lane=lane,
                                scope=scope,
                            )
                            if resolved_backend == "qdrant_sparse_dense_hybrid":
                                sparse_candidates, qdrant_sparse_search_ms = self._sparse_search(
                                    conn=conn,
                                    query=query,
                                    lane=lane,
                                    scope=scope,
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
                        dense_candidates=dense_candidates,
                        sparse_candidates=sparse_candidates,
                        active_backend=resolved_backend,
                        include_explain=include_explain,
                    )
                rerank_ms = 0.0
                if hybrid_config.rerank_top_k > 0 and hits:
                    rerank_started_at = perf_counter()
                    reranked = self._rerank_provider.rerank(
                        query,
                        [hit.to_dict() for hit in hits[: hybrid_config.rerank_top_k]],
                    )
                    rerank_ms = (perf_counter() - rerank_started_at) * 1000.0
                    if reranked:
                        order: dict[tuple[str, int, int], int] = {}
                        for index, candidate in enumerate(reranked):
                            path = candidate.get("path")
                            start_line = candidate.get("startLine", 0)
                            end_line = candidate.get("endLine", 0)
                            if not isinstance(path, str):
                                continue
                            if isinstance(start_line, bool) or not isinstance(start_line, int):
                                continue
                            if isinstance(end_line, bool) or not isinstance(end_line, int):
                                continue
                            order[(path, start_line, end_line)] = index
                        hits.sort(
                            key=lambda hit: order.get(
                                (hit.path, hit.start_line, hit.end_line), len(order)
                            )
                        )
                telemetry_payload: dict[str, Any] = {
                    "requestedBackend": requested_backend,
                    "resolvedBackend": resolved_backend,
                    "fallbackActivated": fallback_activated,
                    "results": len(hits),
                    "qdrantDenseSearchMs": round(qdrant_dense_search_ms, 3),
                    "qdrantSparseSearchMs": round(qdrant_sparse_search_ms, 3),
                    "rerankMs": round(rerank_ms, 3),
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
                    )
                    lane_checks["sparse"] = {"hits": len(sparse_hits), "ms": round(sparse_ms, 3)}
                    if not sparse_hits:
                        errors.append("sparse lane returned no candidates")
                except Exception as err:
                    errors.append(f"sparse lane failed: {err}")

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
    ) -> dict[str, Any]:
        """Append a durable memory entry to the appropriate canonical Markdown file."""
        entry_text = text.strip()
        if not entry_text:
            raise ValueError("text must not be empty")
        resolved_scope = ensure_writable_scope(
            scope or self.config.governance.default_scope, self.config.governance
        )
        target = self._store_target(kind=kind, entity=entity)
        entry_line = self._format_entry_line(
            kind=kind,
            text=entry_text,
            entity=entity,
            confidence=confidence,
            scope=resolved_scope,
        )
        changed = self._append_unique_entry(target, kind=kind, entry_line=entry_line)
        summary = self.reindex()
        return {
            "ok": True,
            "stored": changed,
            "path": resolve_under_workspace(self.config.workspace_root, target),
            "entry": entry_line,
            "scope": resolved_scope,
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
        summary = self.reindex()
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
        return ReflectionSummary(
            proposed=proposed,
            applied=applied,
            pending=pending,
            reflected=reflected,
            index=summary,
        ).to_dict()

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
            if stripped.startswith("- "):
                existing_identity = self._entry_identity(stripped[2:].strip())
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
    ) -> tuple[list[DenseSearchCandidate], float]:
        """Run a dense search query through the configured vector backend."""
        if not self.config.qdrant.enabled or not self.config.embedding.enabled:
            return [], 0.0
        with observed_span(
            "clawops.hypermemory.qdrant.search.dense",
            attributes={
                "lane": lane,
                "scope": scope,
                "candidate_limit": self.config.hybrid.dense_candidate_pool,
            },
        ) as span:
            started_at = perf_counter()
            embedding = self._embed_texts([query.strip()], purpose="query")
            if not embedding:
                return [], 0.0
            try:
                hits = self._qdrant_backend.search_dense(
                    vector=embedding[0],
                    limit=self.config.hybrid.dense_candidate_pool,
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
                "candidate_limit": self.config.hybrid.sparse_candidate_pool,
            },
        ) as span:
            started_at = perf_counter()
            try:
                hits = self._qdrant_backend.search_sparse(
                    vector=sparse_vector.to_qdrant(),
                    limit=self.config.hybrid.sparse_candidate_pool,
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
        return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-" f"{digest[16:20]}-{digest[20:32]}"

    def _slugify(self, value: str) -> str:
        """Return a stable slug for an entity file path."""
        lowered = value.strip().lower()
        slug = "".join(character if character.isalnum() else "-" for character in lowered)
        collapsed = "-".join(part for part in slug.split("-") if part)
        return collapsed or "entity"

    def _sha256(self, value: str) -> str:
        """Return a SHA-256 hex digest for *value*."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
