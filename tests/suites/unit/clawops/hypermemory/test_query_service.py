"""Unit tests for hypermemory/services/query_service.py."""

from __future__ import annotations

import pathlib
import sqlite3
from collections.abc import Sequence

import pytest

from clawops.hypermemory import load_config
from clawops.hypermemory.contracts import CorpusPathStatus, QueryDeps
from clawops.hypermemory.models import (
    HypermemoryConfig,
    IndexedDocument,
    ReindexSummary,
    RerankResponse,
    SearchHit,
)
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.services.query_service import QueryService
from tests.utils.helpers.hypermemory import (
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    build_workspace,
    write_hypermemory_config,
)

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_config(tmp_path: pathlib.Path) -> HypermemoryConfig:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    return load_config(config_path)  # type: ignore[return-value]


class _FakeQueryDeps:
    def __init__(
        self,
        *,
        documents: tuple[IndexedDocument, ...] = (),
        missing_paths: list[CorpusPathStatus] | None = None,
    ) -> None:
        self._documents = documents
        self._missing: list[CorpusPathStatus] = missing_paths or []
        self.reindex_calls: int = 0

    def iter_documents(self) -> tuple[IndexedDocument, ...]:
        return self._documents

    def missing_corpus_paths(self) -> list[CorpusPathStatus]:
        return self._missing

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        del flush_metadata
        self.reindex_calls += 1
        return ReindexSummary(files=0, chunks=0, dirty=False)

    def get_fact(
        self,
        fact_key: str,
        *,
        conn: sqlite3.Connection | None = None,
        scope: str | None = None,
    ) -> SearchHit | None:
        del fact_key, conn, scope
        return None


def _static_rerank_scorer(query: str, docs: Sequence[str]) -> RerankResponse:
    del query
    return RerankResponse(scores=tuple(1.0 for _ in docs), provider="none", applied=True)


def _make_service(
    config: HypermemoryConfig,
    conn: sqlite3.Connection,
    *,
    documents: tuple[IndexedDocument, ...] = (),
) -> tuple[QueryService, _FakeQueryDeps, BackendService, IndexService, FakeQdrantBackend]:
    fake_emb = FakeEmbeddingProvider([0.1, 0.2, 0.3])
    fake_vec = FakeQdrantBackend()
    index = IndexService(connect=lambda: conn)
    backend = BackendService(
        config=config,
        embedding_provider=fake_emb,
        vector_backend=fake_vec,
        index=index,
    )
    deps = _FakeQueryDeps(documents=documents)
    query_deps: QueryDeps = deps  # type: ignore[assignment]
    svc = QueryService(
        config=config,
        connect=lambda: conn,
        backend=backend,
        index=index,
        vector_backend=fake_vec,
        rerank_scorer=_static_rerank_scorer,
        rerank_device_resolver=lambda: "",
        deps=query_deps,
    )
    return svc, deps, backend, index, fake_vec


# ---------------------------------------------------------------------------
# is_dirty
# ---------------------------------------------------------------------------


def test_is_dirty_returns_false_when_db_matches_empty_documents(
    tmp_path: pathlib.Path,
) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, backend, index, _ = _make_service(config, conn, documents=())
    # Pre-seed the fingerprint so the backend config check passes
    index.write_backend_state(conn, "config_fingerprint", backend.backend_fingerprint())
    conn.commit()
    assert not svc.is_dirty()


def test_is_dirty_returns_true_when_db_has_stale_document(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    # Insert a document in DB but provide no documents via deps
    conn.execute(
        "INSERT INTO documents (rel_path, abs_path, lane, source_name, sha256, line_count,"
        " modified_at, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "MEMORY.md",
            "/ws/MEMORY.md",
            "memory",
            "main",
            "stale-sha",
            3,
            "2026-01-01",
            "2026-01-01",
        ),
    )
    conn.commit()
    svc, _, _, _, _ = _make_service(config, conn, documents=())
    assert svc.is_dirty()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_raises_on_non_positive_max_results(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _, _, _ = _make_service(config, conn)
    with pytest.raises(ValueError):
        svc.search("test", max_results=0, auto_index=False)


def test_search_returns_empty_list_on_empty_index(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _, _, _ = _make_service(config, conn)
    hits = svc.search("gateway token", max_results=5, auto_index=False)
    assert hits == []


def test_search_auto_index_triggers_reindex_when_dirty(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    # Insert stale doc so is_dirty() returns True
    conn.execute(
        "INSERT INTO documents (rel_path, abs_path, lane, source_name, sha256, line_count,"
        " modified_at, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
        ("MEMORY.md", "/ws/MEMORY.md", "memory", "main", "stale", 3, "2026-01-01", "2026-01-01"),
    )
    conn.commit()
    svc, deps, _, _, _ = _make_service(config, conn, documents=())
    svc.search("test", max_results=5, auto_index=True)
    assert deps.reindex_calls == 1


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_missing_path_returns_empty_text(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _, _, _ = _make_service(config, conn)
    result = svc.read("nonexistent/path.md")
    assert result["path"] == "nonexistent/path.md"
    assert result["text"] == ""


def test_read_existing_file_returns_content(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _, _, _ = _make_service(config, conn)
    # MEMORY.md exists in the workspace built by _make_config
    result = svc.read("MEMORY.md")
    assert result["path"] == "MEMORY.md"
    assert len(result["text"]) > 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_returns_ok_true(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _, _, _ = _make_service(config, conn)
    status = svc.status()
    assert status["ok"] is True
    assert status["provider"] == "strongclaw-hypermemory"
    assert status["documents"] == 0
    assert status["searchItems"] == 0
