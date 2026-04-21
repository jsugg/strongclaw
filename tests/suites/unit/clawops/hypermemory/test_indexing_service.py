"""Unit tests for hypermemory/services/indexing_service.py."""

from __future__ import annotations

import pathlib
import sqlite3

from clawops.hypermemory import load_config
from clawops.hypermemory.contracts import FlushMetadataResult, IndexingDeps
from clawops.hypermemory.models import HypermemoryConfig
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.services.indexing_service import IndexingService
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


class _FakeIndexingDeps:
    def __init__(self) -> None:
        self.flush_calls: int = 0

    def flush_metadata(self) -> FlushMetadataResult:
        self.flush_calls += 1
        return {"ok": True, "updatedFiles": 0, "updatedEntries": 0}


def _make_service(
    config: HypermemoryConfig,
    conn: sqlite3.Connection,
) -> tuple[IndexingService, _FakeIndexingDeps]:
    fake_emb = FakeEmbeddingProvider([0.1, 0.2, 0.3])
    fake_vec = FakeQdrantBackend()
    index = IndexService(connect=lambda: conn)
    backend = BackendService(
        config=config,
        embedding_provider=fake_emb,
        vector_backend=fake_vec,
        index=index,
    )
    deps = _FakeIndexingDeps()
    indexing_deps: IndexingDeps = deps  # type: ignore[assignment]
    svc = IndexingService(
        config=config,
        connect=lambda: conn,
        backend=backend,
        index=index,
        deps=indexing_deps,
    )
    return svc, deps


# ---------------------------------------------------------------------------
# iter_documents
# ---------------------------------------------------------------------------


def test_iter_documents_returns_memory_file_when_present(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    documents = svc.iter_documents()
    rel_paths = {doc.rel_path for doc in documents}
    assert "MEMORY.md" in rel_paths


def test_iter_documents_includes_daily_log(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    documents = svc.iter_documents()
    # build_workspace creates memory/2026-03-16.md
    rel_paths = {doc.rel_path for doc in documents}
    assert any("memory/" in p for p in rel_paths)


def test_iter_documents_deduplicates_same_rel_path(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    documents = svc.iter_documents()
    rel_paths = [doc.rel_path for doc in documents]
    assert len(rel_paths) == len(set(rel_paths))


def test_iter_documents_returns_tuple(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    result = svc.iter_documents()
    assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# missing_corpus_paths
# ---------------------------------------------------------------------------


def test_missing_corpus_paths_returns_empty_when_all_exist(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    # build_workspace creates docs/ directory — corpus path exists
    assert svc.missing_corpus_paths() == []


def test_missing_corpus_paths_returns_entry_when_dir_absent(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    # Remove the docs directory to simulate a missing corpus path
    workspace = config.workspace_root
    docs_dir = workspace / "docs"
    for child in list(docs_dir.iterdir()):
        child.unlink()
    docs_dir.rmdir()
    svc, _ = _make_service(config, conn)
    missing = svc.missing_corpus_paths()
    assert len(missing) == 1
    assert missing[0]["name"] == "docs"


# ---------------------------------------------------------------------------
# missing_required_corpus_paths
# ---------------------------------------------------------------------------


def test_missing_required_corpus_paths_filters_non_required(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    # Default test config marks corpus paths as non-required (required=False)
    workspace = config.workspace_root
    docs_dir = workspace / "docs"
    for child in list(docs_dir.iterdir()):
        child.unlink()
    docs_dir.rmdir()
    svc, _ = _make_service(config, conn)
    # missing_corpus_paths returns 1 entry, but it's not required
    assert svc.missing_corpus_paths() != []
    assert svc.missing_required_corpus_paths() == []
