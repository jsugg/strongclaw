"""Search and ranking coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
from dataclasses import replace

import pytest

from clawops.hypermemory import HypermemoryEngine, load_config
from tests.fixtures.hypermemory import (
    FailingRerankProvider,
    FakeQdrantBackend,
    StaticRerankProvider,
    build_rerank_workspace,
    build_workspace,
    write_hypermemory_config,
)


def test_hypermemory_scope_filter_and_explain(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()

    engine.store(
        kind="fact",
        text="Global browser-lab recovery stays local-only.",
        scope="project:strongclaw",
    )
    hits = engine.search(
        "browser-lab recovery",
        lane="memory",
        scope="project:strongclaw",
        include_explain=True,
    )

    assert hits
    assert hits[0].scope == "project:strongclaw"
    payload = hits[0].to_dict()
    assert payload["explain"]["lexicalScore"] > 0
    assert payload["scope"] == "project:strongclaw"


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
    assert payload["rerankOperationalRequired"] is True
    assert payload["qdrantEnabled"] is True
    assert payload["qdrantHealthy"] is True
    assert payload["vectorItems"] == 0
    assert payload["lastVectorSyncAt"]
    assert payload["missingCorpusPaths"] == []


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
    assert hits[0].to_dict()["explain"]["rerankScore"] == pytest.approx(1.0)
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
    assert hits[0].to_dict()["explain"]["rerankScore"] == pytest.approx(0.0)


def test_hypermemory_get_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))

    assert engine.read("memory/2099-01-01.md") == {"path": "memory/2099-01-01.md", "text": ""}
