"""Observability tests for StrongClaw hypermemory."""

from __future__ import annotations

import json
import pathlib
from dataclasses import replace

import pytest

from clawops import observability
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
from tests.utils.helpers.observability import RecordingExporter


def _configure_engine(tmp_path: pathlib.Path) -> tuple[HypermemoryEngine, FakeQdrantBackend]:
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
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-observability"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        vector_backend=fake_qdrant,
    )
    return engine, fake_qdrant


def _configure_rerank_engine(
    tmp_path: pathlib.Path,
    *,
    rerank_provider: object | None = None,
) -> tuple[HypermemoryEngine, FakeQdrantBackend]:
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
        rerank=replace(
            config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            fail_open=True,
            local=replace(config.rerank.local, model="BAAI/bge-reranker-v2-m3"),
        ),
        hybrid=replace(config.hybrid, rerank_candidate_pool=2),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-rerank-observability"),
    )
    fake_qdrant = FakeQdrantBackend()
    engine = HypermemoryEngine(
        config,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0, 0.0]),
        rerank_provider=rerank_provider,
        vector_backend=fake_qdrant,
    )
    return engine, fake_qdrant


def test_hypermemory_emits_structured_logs_for_dense_search(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_engine(tmp_path)
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    hits = engine.search("credential rollover checklist", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    event_names = {record["event"] for record in stderr_lines}
    assert "clawops.hypermemory.embedding" in event_names
    assert "clawops.hypermemory.qdrant.search.dense" in event_names
    assert "clawops.hypermemory.search" in event_names
    assert "clawops.hypermemory.vector_sync" in event_names


def test_hypermemory_search_exports_trace_spans(
    tmp_path: pathlib.Path,
    tracing_exporter: RecordingExporter,
) -> None:
    engine, fake_qdrant = _configure_engine(tmp_path)
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    engine.search("credential rollover checklist", lane="all")
    observability.force_flush()

    span_names = {span.name for span in tracing_exporter.spans}
    assert "clawops.hypermemory.reindex" in span_names
    assert "clawops.hypermemory.vector_sync" in span_names
    assert "clawops.hypermemory.search" in span_names
    assert "clawops.hypermemory.qdrant.search.dense" in span_names
    search_span = next(
        span for span in tracing_exporter.spans if span.name == "clawops.hypermemory.search"
    )
    assert search_span.attributes["resolvedBackend"] == "qdrant_dense_hybrid"
    assert search_span.attributes["results"] >= 1


def test_hypermemory_logs_fallback_activation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_engine(tmp_path)
    fake_qdrant.raise_on_search = True
    engine.reindex()

    hits = engine.search("gateway token", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    assert hits[0].backend == "sqlite_fts"
    fallback_log = next(
        record
        for record in stderr_lines
        if record["event"] == "clawops.hypermemory.search.fallback"
    )
    assert fallback_log["resolvedBackend"] == "sqlite_fts"


def test_hypermemory_logs_sparse_candidate_counts_for_hypermemory_search(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
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
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-observability"),
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
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]
    fake_qdrant.sparse_search_results = [
        SparseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=1.4)
    ]

    hits = engine.search("gateway token", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    search_log = next(
        record for record in stderr_lines if record["event"] == "clawops.hypermemory.search"
    )
    assert search_log["resolvedBackend"] == "qdrant_sparse_dense_hybrid"
    assert search_log["sparseCandidates"] >= 1
    assert search_log["qdrantDenseSearchMs"] >= 0.0
    assert search_log["qdrantSparseSearchMs"] >= 0.0
    assert "qdrantDenseMs" not in search_log
    assert "qdrantSparseMs" not in search_log


def test_hypermemory_emits_rerank_logs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_rerank_engine(
        tmp_path,
        rerank_provider=StaticRerankProvider((0.2, 0.4)),
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    hits = engine.search("credential rollover checklist", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    rerank_log = next(
        record for record in stderr_lines if record["event"] == "clawops.hypermemory.rerank"
    )
    assert rerank_log["provider"] == "local-sentence-transformers"
    assert rerank_log["applied"] is True
    assert rerank_log["candidateCount"] >= 1
    search_log = next(
        record for record in stderr_lines if record["event"] == "clawops.hypermemory.search"
    )
    assert search_log["rerankApplied"] is True
    assert search_log["rerankProvider"] == "local-sentence-transformers"


def test_hypermemory_emits_rerank_error_logs_and_spans_on_fail_open(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracing_exporter: RecordingExporter,
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_rerank_engine(
        tmp_path,
        rerank_provider=FailingRerankProvider(),
    )
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    hits = engine.search("credential rollover checklist", lane="all")
    observability.force_flush()
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    rerank_error = next(
        record for record in stderr_lines if record["event"] == "clawops.hypermemory.rerank.error"
    )
    assert rerank_error["error"] == "rerank backend unavailable"
    search_log = next(
        record for record in stderr_lines if record["event"] == "clawops.hypermemory.search"
    )
    assert search_log["rerankFailOpen"] is True
    rerank_span = next(
        span for span in tracing_exporter.spans if span.name == "clawops.hypermemory.rerank"
    )
    assert rerank_span.attributes["candidateCount"] >= 1
