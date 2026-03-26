"""Backend-selection coverage for the StrongClaw hypermemory engine."""

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
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    build_workspace,
    write_hypermemory_config,
)

pytestmark = pytest.mark.qdrant


def test_hypermemory_hybrid_search_uses_dense_backend(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-test"),
    )
    fake_embedder = FakeEmbeddingProvider([1.0, 0.0, 0.0])
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=fake_embedder,
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

    hits = engine.search("credential rollover checklist", lane="all")

    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert hits[0].backend == "qdrant_dense_hybrid"
    assert fake_qdrant.ensure_calls
    assert fake_qdrant.upsert_calls


def test_hypermemory_search_uses_runtime_candidate_pool_overrides(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-test"),
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
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    hits = engine.search(
        "gateway token",
        lane="all",
        dense_candidate_pool=7,
        sparse_candidate_pool=5,
    )

    assert hits
    assert fake_qdrant.dense_limits[-1] == 7
    assert fake_qdrant.sparse_limits[-1] == 5


def test_hypermemory_dense_backend_falls_back_to_sqlite(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-test"),
    )
    fake_qdrant = FakeQdrantBackend()
    fake_qdrant.raise_on_search = True
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    engine.reindex()

    hits = engine.search("gateway token", lane="all")

    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert hits[0].backend == "sqlite_fts"


def test_hypermemory_status_and_verify_report_sparse_backend_state(
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
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.92)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.7)
    ]

    status = engine.status()
    verification = engine.verify()

    assert status["backendActive"] == "qdrant_sparse_dense_hybrid"
    assert status["sparseVectorItems"] >= 1
    assert status["sparseFingerprint"]
    assert status["sparseFingerprintDirty"] is False
    assert verification["ok"] is True
    assert verification["laneChecks"]["dense"]["hits"] >= 1
    assert verification["laneChecks"]["sparse"]["hits"] >= 1
    assert fake_qdrant.include_sparse_calls == [True]
