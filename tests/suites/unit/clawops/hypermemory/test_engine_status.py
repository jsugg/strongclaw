"""Status and verification coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
import textwrap
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, load_config
from clawops.typed_values import as_mapping
from tests.utils.helpers.hypermemory import (
    FailingRerankProvider,
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    StaticRerankProvider,
    build_rerank_workspace,
    build_workspace,
    write_hypermemory_config,
)


def test_hypermemory_status_reports_dense_and_rerank_configuration(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        hybrid=replace(config.hybrid, rerank_candidate_pool=32),
        qdrant=replace(config.qdrant, enabled=True, collection="hypermemory-status"),
        rerank=replace(
            config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            fallback_provider="compatible-http",
            local=replace(config.rerank.local, model="rerank-test"),
            compatible_http=replace(
                config.rerank.compatible_http,
                model="http-rerank-test",
            ),
        ),
    )
    engine = HypermemoryEngine(config, vector_backend=FakeQdrantBackend())
    engine.reindex()

    payload = engine.status()

    assert payload["backendActive"] == "sqlite_fts"
    assert payload["backendFallback"] == "sqlite_fts"
    assert payload["embeddingProvider"] == "disabled"
    assert payload["rerankProvider"] == "local-sentence-transformers"
    assert payload["rerankFallbackProvider"] == "compatible-http"
    assert payload["rerankFailOpen"] is True
    assert payload["rerankModel"] == "rerank-test"
    assert payload["rerankDevice"] == "auto"
    assert payload["rerankResolvedDevice"] in {"cpu", "cuda", "mps"}
    assert payload["rerankFallbackModel"] == "http-rerank-test"
    assert payload["rerankCandidatePool"] == 32
    assert payload["rerankOperationalRequired"] is False
    assert payload["qdrantEnabled"] is True
    assert payload["qdrantHealthy"] is True
    assert payload["vectorItems"] == 0
    assert payload["lastVectorSyncAt"]
    assert payload["missingCorpusPaths"] == []


def test_hypermemory_status_marks_rerank_operational_when_fail_closed(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(
        config,
        hybrid=replace(config.hybrid, rerank_candidate_pool=32),
        rerank=replace(
            config.rerank,
            enabled=True,
            fail_open=False,
            provider="local-sentence-transformers",
            fallback_provider="compatible-http",
        ),
    )
    engine = HypermemoryEngine(config)
    engine.reindex()

    payload = engine.status()

    assert payload["rerankEnabled"] is True
    assert payload["rerankFailOpen"] is False
    assert payload["rerankOperationalRequired"] is True


def test_hypermemory_rerank_changes_planner_order_before_diversity(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_rerank_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    baseline_config = load_config(config_path)
    baseline_engine = HypermemoryEngine(baseline_config)
    baseline_engine.reindex()
    baseline_hits = baseline_engine.search(
        "gateway token deploy checklist",
        lane="all",
        include_explain=True,
    )
    assert baseline_hits
    assert baseline_hits[0].path == "MEMORY.md"

    rerank_config = replace(
        baseline_config,
        ranking=replace(baseline_config.ranking, rerank_weight=0.95),
        hybrid=replace(baseline_config.hybrid, rerank_candidate_pool=3),
        rerank=replace(
            baseline_config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            local=replace(
                baseline_config.rerank.local,
                model="BAAI/bge-reranker-v2-m3",
            ),
        ),
    )
    rerank_provider = StaticRerankProvider([0.0, 0.4, 1.0])
    rerank_engine = HypermemoryEngine(rerank_config, rerank_provider=rerank_provider)
    rerank_engine.reindex()

    hits = rerank_engine.search(
        "gateway token deploy checklist",
        lane="all",
        include_explain=True,
    )

    assert hits
    assert hits[0].path != baseline_hits[0].path
    explain = as_mapping(hits[0].to_dict()["explain"], path="hits[0].explain")
    rerank_score = explain.get("rerankScore")
    assert isinstance(rerank_score, (int, float))
    assert abs(float(rerank_score) - 1.0) < 1e-9
    assert rerank_provider.calls


def test_hypermemory_rerank_fail_open_preserves_provisional_order(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_rerank_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    baseline_config = load_config(config_path)
    baseline_engine = HypermemoryEngine(baseline_config)
    baseline_engine.reindex()
    baseline_hits = baseline_engine.search("gateway token deploy checklist", lane="all")
    assert baseline_hits

    fail_open_config = replace(
        baseline_config,
        hybrid=replace(baseline_config.hybrid, rerank_candidate_pool=2),
        rerank=replace(
            baseline_config.rerank,
            enabled=True,
            provider="local-sentence-transformers",
            fail_open=True,
            local=replace(
                baseline_config.rerank.local,
                model="BAAI/bge-reranker-v2-m3",
            ),
        ),
    )
    fail_open_engine = HypermemoryEngine(
        fail_open_config,
        rerank_provider=FailingRerankProvider(),
    )
    fail_open_engine.reindex()

    hits = fail_open_engine.search(
        "gateway token deploy checklist",
        lane="all",
        include_explain=True,
    )

    assert [hit.path for hit in hits] == [hit.path for hit in baseline_hits]
    explain = as_mapping(hits[0].to_dict()["explain"], path="hits[0].explain")
    rerank_score = explain.get("rerankScore")
    assert isinstance(rerank_score, (int, float))
    assert abs(float(rerank_score) - 0.0) < 1e-9


def test_hypermemory_status_reports_missing_optional_corpus_paths(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory-optional-missing.yaml"
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
                  required: true
                - name: upstream
                  path: repo/upstream
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )

    engine = HypermemoryEngine(load_config(config_path))
    payload = engine.status()

    assert payload["missingCorpusPaths"] == [
        {
            "name": "upstream",
            "path": str((workspace / "repo" / "upstream").resolve()),
            "pattern": "**/*.md",
            "required": False,
        }
    ]


def test_hypermemory_reindex_soft_fails_missing_required_corpus_path(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory-required-missing.yaml"
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
                  required: true
                - name: upstream
                  path: repo/upstream
                  pattern: "**/*.md"
                  required: true
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )

    engine = HypermemoryEngine(load_config(config_path))

    summary = engine.reindex()
    payload = engine.status()
    verification = engine.verify()

    assert summary.files >= 1
    assert payload["missingCorpusPaths"] == [
        {
            "name": "upstream",
            "path": str((workspace / "repo" / "upstream").resolve()),
            "pattern": "**/*.md",
            "required": True,
        }
    ]
    assert verification["ok"] is False
    assert "required corpus paths are missing: upstream" in verification["errors"]


def test_hypermemory_status_reports_deferred_vector_sync(tmp_path: pathlib.Path) -> None:
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
    fake_qdrant.raise_on_ensure_collection = True

    payload = engine.store(kind="fact", text="Deferred sync still preserves local recall.")
    status = engine.status()

    assert payload["vectorSyncDeferred"] is True
    assert status["vectorSyncDeferred"] is True
    assert status["lastVectorSyncError"] == "qdrant collection warmup timed out"
