"""Unit tests for hypermemory/services/backend_service.py."""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import replace

from clawops.hypermemory import load_config
from clawops.hypermemory.contracts import VectorRow
from clawops.hypermemory.models import HypermemoryConfig
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.sparse import build_sparse_encoder
from tests.utils.helpers.hypermemory import (
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    build_workspace,
    write_hypermemory_config,
)


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


def _make_service(
    config: HypermemoryConfig,
    conn: sqlite3.Connection,
    *,
    embedding_vector: list[float] | None = None,
    raise_on_search: bool = False,
) -> tuple[BackendService, FakeEmbeddingProvider, FakeQdrantBackend]:
    vector = embedding_vector or [0.1, 0.2, 0.3]
    fake_emb = FakeEmbeddingProvider(vector)
    fake_vec = FakeQdrantBackend()
    fake_vec.raise_on_search = raise_on_search
    index = IndexService(connect=lambda: conn)
    svc = BackendService(
        config=config,
        embedding_provider=fake_emb,
        vector_backend=fake_vec,
        index=index,
    )
    return svc, fake_emb, fake_vec


def test_backend_uses_qdrant_fts_returns_false(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # Default test config uses sqlite_fts backend
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    assert not svc.backend_uses_qdrant()


def test_backend_uses_qdrant_dense_returns_true(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid"),
    )
    conn = _make_conn()
    svc, _, _ = _make_service(cfg, conn)
    assert svc.backend_uses_qdrant()


def test_backend_uses_sparse_vectors_false_for_fts(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    assert not svc.backend_uses_sparse_vectors()


def test_backend_uses_sparse_vectors_true_for_hybrid(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid"),
    )
    conn = _make_conn()
    svc, _, _ = _make_service(cfg, conn)
    assert svc.backend_uses_sparse_vectors()


def test_backend_fingerprint_is_consistent(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    fp1 = svc.backend_fingerprint()
    fp2 = svc.backend_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex digest


def test_backend_fingerprint_changes_with_config(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc_a, _, _ = _make_service(config, conn)
    cfg_b = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid"),
    )
    svc_b, _, _ = _make_service(cfg_b, conn)
    assert svc_a.backend_fingerprint() != svc_b.backend_fingerprint()


def test_embedding_batches_produces_correct_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # Set small batch_size to ensure multiple batches
    cfg = replace(
        config,
        embedding=replace(config.embedding, batch_size=3),
    )
    conn = _make_conn()
    svc, _, _ = _make_service(cfg, conn)
    rows: list[VectorRow] = [
        {
            "item_id": i,
            "point_id": f"pt-{i}",
            "content": f"document {i}",
            "payload": {
                "item_id": i,
                "rel_path": "MEMORY.md",
                "lane": "memory",
                "source_name": "main",
                "item_type": "fact",
                "scope": "global",
                "start_line": i,
                "end_line": i,
                "modified_at": "2026-01-01",
                "confidence": None,
            },
        }
        for i in range(7)
    ]
    batches = list(svc.embedding_batches(rows))
    assert len(batches) == 3  # 3, 3, 1
    assert len(batches[0]) == 3
    assert len(batches[1]) == 3
    assert len(batches[2]) == 1


def test_embedding_batches_empty_input(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    assert list(svc.embedding_batches([])) == []


def test_embed_texts_calls_provider(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    vector = [0.1, 0.2, 0.3]
    svc, fake_emb, _ = _make_service(config, conn, embedding_vector=vector)
    result = svc.embed_texts(["hello", "world"], purpose="test")
    assert len(result) == 2
    assert len(fake_emb.calls) == 1
    assert fake_emb.calls[0] == ["hello", "world"]


def test_embed_texts_returns_vectors_matching_input_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    result = svc.embed_texts(["a", "b", "c"], purpose="index")
    assert len(result) == 3


def test_dense_search_returns_empty_when_disabled(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # Default config has embedding.enabled=False / qdrant.enabled=False
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    hits, elapsed = svc.dense_search(
        query="gateway token",
        lane="memory",
        scope=None,
        candidate_limit=10,
    )
    assert hits == []
    assert elapsed == 0.0


def test_dense_search_calls_vector_backend_when_enabled(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    from clawops.hypermemory.models import DenseSearchCandidate

    cfg = replace(
        config,
        embedding=replace(config.embedding, enabled=True),
        qdrant=replace(config.qdrant, enabled=True),
    )
    conn = _make_conn()
    fake_cand = DenseSearchCandidate(item_id=1, score=0.9, point_id="pt-1")
    svc, _, fake_vec = _make_service(cfg, conn)
    fake_vec.search_results = [fake_cand]
    hits, _ = svc.dense_search(
        query="gateway token",
        lane="memory",
        scope=None,
        candidate_limit=5,
    )
    assert len(hits) == 1
    assert hits[0].item_id == 1
    assert len(fake_vec.dense_limits) == 1
    assert fake_vec.dense_limits[0] == 5


def test_sync_vectors_disabled_clears_vector_items(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # Default config has qdrant disabled
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    sparse_encoder = build_sparse_encoder([])
    # Insert parent rows then a stale vector item to confirm it gets cleared
    conn.execute(
        "INSERT INTO documents (rel_path, abs_path, lane, source_name, sha256,"
        " line_count, modified_at, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
        ("MEMORY.md", "/ws/MEMORY.md", "memory", "main", "abc", 3, "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO search_items "
        "(document_id, rel_path, lane, source_name, source_kind, item_type, title, snippet, "
        "normalized_text, start_line, end_line, scope, modified_at, "
        "contradiction_count, evidence_count, entities_json, evidence_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1,
            "MEMORY.md",
            "memory",
            "main",
            "markdown",
            "fact",
            "t",
            "s",
            "s",
            1,
            1,
            "global",
            "2026-01-01",
            0,
            0,
            "[]",
            "[]",
        ),
    )
    stale_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO vector_items"
        " (item_id, point_id, embedding_model, embedding_dim, content_sha256, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        (stale_id, "stale-pt", "all-MiniLM-L6-v2", 384, "abc", "2026-01-01"),
    )
    conn.commit()
    svc.sync_vectors(
        conn=conn,
        vector_rows=[],
        stale_point_ids=set(),
        sparse_encoder=sparse_encoder,
    )
    count = int(conn.execute("SELECT COUNT(*) FROM vector_items").fetchone()[0])
    assert count == 0


def test_sync_vectors_disabled_writes_config_fingerprint(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _, _ = _make_service(config, conn)
    index = IndexService(connect=lambda: conn)
    sparse_encoder = build_sparse_encoder([])
    svc.sync_vectors(
        conn=conn,
        vector_rows=[],
        stale_point_ids=set(),
        sparse_encoder=sparse_encoder,
    )
    fp = index.backend_state_value(conn, "config_fingerprint")
    assert fp == svc.backend_fingerprint()
