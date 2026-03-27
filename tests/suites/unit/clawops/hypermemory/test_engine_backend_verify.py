"""Backend verification and failure-mode coverage for the hypermemory engine."""

from __future__ import annotations

import pathlib
from dataclasses import replace

import pytest

from clawops.hypermemory import (
    DenseSearchCandidate,
    HypermemoryEngine,
    SparseSearchCandidate,
    load_config,
)
from tests.utils.helpers.hypermemory import (
    FailingRerankProvider,
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    StaticRerankProvider,
    build_workspace,
    write_hypermemory_config,
)

pytestmark = pytest.mark.qdrant


def test_hypermemory_verify_requires_an_operational_rerank_provider(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        hybrid=replace(config.hybrid, rerank_candidate_pool=2),
        rerank=replace(
            config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            local=replace(config.rerank.local, model="BAAI/bge-reranker-v2-m3"),
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        rerank_provider=StaticRerankProvider([0.8, 0.2]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is True
    rerank_lane = verification["laneChecks"].get("rerank")
    assert rerank_lane is not None
    assert rerank_lane.get("provider") == "local-sentence-transformers"
    assert rerank_lane.get("candidateCount") == 2


def test_hypermemory_verify_fails_when_rerank_provider_is_not_operational(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        hybrid=replace(config.hybrid, rerank_candidate_pool=2),
        rerank=replace(
            config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            local=replace(config.rerank.local, model="BAAI/bge-reranker-v2-m3"),
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        rerank_provider=FailingRerankProvider(),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is False
    assert "rerank provider failed: rerank backend unavailable" in verification["errors"]


def test_hypermemory_verify_fails_when_sparse_state_is_stale(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO backend_state(key, value) VALUES ('sparse_fingerprint', 'stale')"
        )
        conn.commit()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is False
    assert "sparse fingerprint is dirty" in verification["errors"]


def test_hypermemory_verify_fails_when_qdrant_is_unhealthy(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    fake_qdrant.health_payload = {"enabled": True, "healthy": False, "collection": "test"}
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is False
    assert "Qdrant must be enabled and healthy" in verification["errors"]


def test_hypermemory_verify_fails_when_vector_sync_error_is_present(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO backend_state(key, value) VALUES ('last_sync_error', ?)",
            ("dense lane drift",),
        )
        conn.commit()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is False
    assert "vector sync error: dense lane drift" in verification["errors"]


def test_hypermemory_reindex_surfaces_vector_sync_errors(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    fake_qdrant.raise_on_ensure_collection = True
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )

    with pytest.raises(RuntimeError, match="qdrant collection warmup timed out"):
        engine.reindex()

    status = engine.status()

    assert status["vectorItems"] == 0
    assert status["lastVectorSyncError"] == "qdrant collection warmup timed out"


def test_hypermemory_verify_fails_when_collection_lacks_sparse_lane(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory"),
    )
    fake_qdrant = FakeQdrantBackend()
    fake_qdrant.collection_details_payload = {
        "config": {
            "params": {
                "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
            }
        }
    }
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    verification = engine.verify()

    assert verification["ok"] is False
    assert (
        "Qdrant collection is missing the named dense or sparse vector lane"
        in verification["errors"]
    )
