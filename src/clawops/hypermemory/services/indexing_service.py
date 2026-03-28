"""Indexing service for the StrongClaw hypermemory engine."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from clawops.hypermemory.canonical_store_helpers import fact_category, proposal_kind, store_target
from clawops.hypermemory.config import matches_glob, resolve_under_workspace
from clawops.hypermemory.contracts import CorpusPathStatus, IndexingDeps, VectorRow
from clawops.hypermemory.models import (
    EvidenceEntry,
    HypermemoryConfig,
    IndexedDocument,
    ParsedItem,
    ReindexSummary,
)
from clawops.hypermemory.parser import build_document
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.utils import normalized_retrieval_text, point_id, sha256
from clawops.observability import emit_structured_log, observed_span


class DeferredVectorSyncError(RuntimeError):
    """Raised when local reindex succeeds but vector synchronization is deferred."""

    def __init__(self, message: str, *, summary: ReindexSummary) -> None:
        super().__init__(message)
        self.summary = summary


class IndexingService:
    """Stateful owner for document discovery and derived-index rebuilds."""

    def __init__(
        self,
        *,
        config: HypermemoryConfig,
        connect: Callable[[], sqlite3.Connection],
        backend: BackendService,
        index: IndexService,
        deps: IndexingDeps,
    ) -> None:
        self._config = config
        self._connect = connect
        self._backend = backend
        self._index = index
        self._deps = deps

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
        if flush_metadata:
            self._deps.flush_metadata()
        documents = self.iter_documents()
        sparse_encoder = self._backend.sparse_encoder_for_documents(documents)
        typed_counts: defaultdict[str, int] = defaultdict(int)
        vector_rows: list[VectorRow] = []
        with observed_span(
            "clawops.hypermemory.reindex",
            attributes={
                "documents": len(documents),
                "backend": self._config.backend.active,
                "qdrant_enabled": self._config.qdrant.enabled,
            },
        ) as span:
            try:
                with self._connect() as conn:
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
                    if self._backend.backend_uses_sparse_vectors():
                        dirty = dirty or (
                            self._index.backend_state_value(conn, "sparse_fingerprint")
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
                                    normalized_retrieval_text(item.title, item.snippet),
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
                                    "point_id": point_id(
                                        document_rel_path=document.rel_path,
                                        item_type=item.item_type,
                                        start_line=item.start_line,
                                        end_line=item.end_line,
                                        snippet=item.snippet,
                                    ),
                                    "content": normalized_retrieval_text(item.title, item.snippet),
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
                with self._connect() as conn:
                    try:
                        self._backend.sync_vectors(
                            conn=conn,
                            vector_rows=vector_rows,
                            stale_point_ids=existing_point_ids,
                            sparse_encoder=sparse_encoder,
                        )
                    except Exception as err:
                        raise DeferredVectorSyncError(str(err), summary=summary) from err
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
                        "backend": self._config.backend.active,
                        "error": str(err),
                    },
                )
                raise

    def iter_documents(self) -> tuple[IndexedDocument, ...]:
        """Return the current canonical document set."""
        unique_documents: list[IndexedDocument] = []
        suppressed_duplicates: dict[str, list[str]] = {}
        source_by_path: dict[str, str] = {}
        for document in self._iter_documents():
            if document.rel_path in source_by_path:
                suppressed_duplicates.setdefault(
                    document.rel_path, [source_by_path[document.rel_path]]
                ).append(document.source_name)
                continue
            source_by_path[document.rel_path] = document.source_name
            unique_documents.append(document)
        if suppressed_duplicates:
            duplicate_paths = sorted(suppressed_duplicates)
            emit_structured_log(
                "clawops.hypermemory.index.duplicate_documents",
                {
                    "duplicates": len(suppressed_duplicates),
                    "duplicatePaths": ",".join(duplicate_paths[:10]),
                    "duplicatePathOverflow": max(len(duplicate_paths) - 10, 0),
                },
            )
        return tuple(unique_documents)

    def missing_corpus_paths(self) -> list[CorpusPathStatus]:
        """Report configured corpus paths that are not currently available."""
        missing: list[CorpusPathStatus] = []
        for source in self._config.corpus_paths:
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

    def missing_required_corpus_paths(self) -> list[CorpusPathStatus]:
        """Return missing corpus entries that are marked required."""
        return [entry for entry in self.missing_corpus_paths() if entry["required"]]

    def _iter_documents(self) -> Iterator[IndexedDocument]:
        yield from self._iter_memory_documents()
        yield from self._iter_corpus_documents()

    def _iter_memory_documents(self) -> Iterator[IndexedDocument]:
        if self._config.include_default_memory:
            for file_name in self._config.memory_file_names:
                path = self._config.workspace_root / file_name
                if path.exists():
                    yield build_document(
                        workspace_root=self._config.workspace_root,
                        path=path,
                        lane="memory",
                        source_name="memory",
                        default_scope=self._config.governance.default_scope,
                    )
        daily_dir = self._config.workspace_root / self._config.daily_dir
        if daily_dir.exists():
            for path in sorted(daily_dir.glob("*.md")):
                yield build_document(
                    workspace_root=self._config.workspace_root,
                    path=path,
                    lane="memory",
                    source_name="daily",
                    default_scope=self._config.governance.default_scope,
                )
        bank_dir = self._config.workspace_root / self._config.bank_dir
        if bank_dir.exists():
            for path in sorted(bank_dir.rglob("*.md")):
                yield build_document(
                    workspace_root=self._config.workspace_root,
                    path=path,
                    lane="memory",
                    source_name="bank",
                    default_scope=self._config.governance.default_scope,
                )

    def _iter_corpus_documents(self) -> Iterator[IndexedDocument]:
        seen_rel_paths: dict[str, str] = {}

        def _build_corpus_document(
            path: pathlib.Path,
            *,
            rel_path: str,
            source_name: str,
        ) -> IndexedDocument | None:
            kept_source = seen_rel_paths.get(rel_path)
            if kept_source is not None:
                emit_structured_log(
                    "clawops.hypermemory.corpus.duplicate_document",
                    {
                        "relPath": rel_path,
                        "keptSource": kept_source,
                        "skippedSource": source_name,
                    },
                )
                return None
            seen_rel_paths[rel_path] = source_name
            return build_document(
                workspace_root=self._config.workspace_root,
                path=path,
                lane="corpus",
                source_name=source_name,
                default_scope=self._config.governance.default_scope,
            )

        for source in self._config.corpus_paths:
            if not source.path.exists():
                continue
            if source.path.is_file():
                rel_path = resolve_under_workspace(self._config.workspace_root, source.path)
                if matches_glob(rel_path, source.pattern):
                    document = _build_corpus_document(
                        source.path,
                        rel_path=rel_path,
                        source_name=source.name,
                    )
                    if document is not None:
                        yield document
                continue
            for path in sorted(source.path.rglob("*.md")):
                rel_path = resolve_under_workspace(self._config.workspace_root, path)
                if matches_glob(rel_path, source.pattern):
                    document = _build_corpus_document(
                        path=path,
                        rel_path=rel_path,
                        source_name=source.name,
                    )
                    if document is not None:
                        yield document

    def _clear_derived_rows(self, conn: sqlite3.Connection) -> None:
        """Clear the rebuildable tables before a full reindex."""
        for table_name in (
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
        item: ParsedItem,
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
                (item_id, link_key[0], link_key[1], link_key[2], link_key[3]),
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
            proposal_id = item.proposal_id or sha256(item.snippet)
            target_kind = proposal_kind(item.snippet, config=self._config)
            target = store_target(
                self._config,
                kind=target_kind,
                entity=next(iter(item.entities), None),
            )
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
                    target_kind,
                    item.scope,
                    item.proposal_status or "pending",
                    item.snippet,
                    document_rel_path,
                    item.start_line,
                    resolve_under_workspace(self._config.workspace_root, target),
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

    def _rebuild_fact_registry(self, conn: sqlite3.Connection) -> None:
        """Rebuild the current-state fact registry from indexed search items."""
        conn.execute("DELETE FROM fact_registry")
        if not self._config.fact_registry.enabled:
            return
        rows = conn.execute("""
            SELECT id, fact_key, rel_path, start_line, modified_at
            FROM search_items
            WHERE fact_key IS NOT NULL
              AND invalidated_at IS NULL
            ORDER BY rel_path ASC, start_line ASC, id ASC
            """).fetchall()
        registry: dict[str, tuple[int, list[int], str]] = {}
        for row in rows:
            fact_key_value = str(row["fact_key"])
            current = registry.get(fact_key_value)
            if current is None:
                registry[fact_key_value] = (int(row["id"]), [], str(row["modified_at"]))
                continue
            current_item_id, history, _last_updated = current
            history = list(history)
            history.append(current_item_id)
            registry[fact_key_value] = (int(row["id"]), history, str(row["modified_at"]))
        for fact_key_value, (current_item_id, history, last_updated) in registry.items():
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
                    fact_key_value,
                    current_item_id,
                    fact_category(fact_key_value),
                    last_updated,
                    len(history) + 1,
                    json.dumps(history, sort_keys=True),
                ),
            )
