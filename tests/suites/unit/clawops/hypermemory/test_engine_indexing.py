"""Indexing and search coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib

from clawops.hypermemory import HypermemoryEngine, default_config_path, load_config
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config


def test_load_shipped_hypermemory_config() -> None:
    config = load_config(default_config_path())
    assert config.include_default_memory is True
    assert config.db_path.name == "hypermemory.sqlite"
    assert any(entry.name == "runbooks" for entry in config.corpus_paths)
    assert any(entry.name == "openclaw-workspaces" for entry in config.corpus_paths)
    assert config.backend.active == "sqlite_fts"
    assert config.hybrid.fusion == "rrf"
    assert config.hybrid.rerank_candidate_pool == 32
    assert config.rerank.enabled is True
    assert config.rerank.provider == "local-sentence-transformers"
    assert config.rerank.fallback_provider == "compatible-http"
    assert config.rerank.local.device == "auto"
    assert config.qdrant.enabled is False
    assert config.dedup.enabled is True
    assert config.fact_registry.enabled is True
    assert config.noise.enabled is True


def test_hypermemory_reindex_and_search(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    engine = HypermemoryEngine(load_config(config_path))
    summary = engine.reindex()

    assert summary.files >= 3
    assert summary.chunks >= 3

    hits = engine.search("gateway token", lane="all")
    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert "Rotate the gateway token" in hits[0].snippet


def test_hypermemory_store_update_and_reflect(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()

    store_result = engine.store(kind="fact", text="Deploy approvals require two reviewers.")
    world_path = workspace / "bank" / "world.md"
    assert store_result["stored"] is True
    assert "two reviewers" in world_path.read_text(encoding="utf-8")

    update_result = engine.update(
        rel_path="bank/world.md",
        find_text="two reviewers",
        replace_text="three reviewers",
    )
    assert update_result["replacements"] == 1
    assert "three reviewers" in world_path.read_text(encoding="utf-8")

    reflect_result = engine.reflect()
    assert reflect_result["reflected"]["fact"] == 1
    assert reflect_result["reflected"]["opinion"] == 1
    assert reflect_result["reflected"]["entity"] == 1
    assert reflect_result["proposed"] >= 3
    assert (workspace / "bank" / "opinions.md").exists()
    assert (workspace / "bank" / "entities" / "alice.md").exists()
    assert (workspace / "bank" / "proposals.md").exists()


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


def test_hypermemory_reflect_global_scope_becomes_pending_proposal(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    (workspace / "memory" / "2026-03-17.md").write_text(
        """
        # Daily Log

        ## Retain
        - Fact[scope=global]: Shared browser lab access remains disabled.
        """.strip() + "\n",
        encoding="utf-8",
    )
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()

    payload = engine.reflect(mode="safe")
    proposals_text = (workspace / "bank" / "proposals.md").read_text(encoding="utf-8")
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert payload["pending"] >= 1
    assert "scope=global" in proposals_text
    assert "Shared browser lab access remains disabled." not in world_text


def test_hypermemory_benchmark_runner(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()

    payload = engine.benchmark_cases(
        [
            {
                "name": "runbook",
                "query": "gateway token",
                "expectedPaths": ["docs/runbook.md"],
                "lane": "corpus",
            }
        ]
    )

    assert payload["provider"] == "strongclaw-hypermemory"
    assert payload["passed"] == 1
    assert payload["cases"][0]["passed"] is True
