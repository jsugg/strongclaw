"""Tests for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
import textwrap
from dataclasses import replace

import pytest

from clawops.hypermemory import (
    HypermemoryEngine,
    default_config_path,
    load_config,
)
from tests.fixtures.hypermemory import (
    FailingRerankProvider,
    FakeQdrantBackend,
    StaticRerankProvider,
    build_rerank_workspace,
    build_workspace,
    write_hypermemory_config,
)


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


def test_hypermemory_export_memory_pro_defaults_to_durable_surfaces(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()
    engine.reflect()
    engine.store(
        kind="reflection",
        text="Prefer canary rollouts for gateway migrations.",
        scope="project:strongclaw",
    )

    payload = engine.export_memory_pro_import(scope="project:strongclaw")

    assert payload["provider"] == "strongclaw-hypermemory"
    assert payload["scope"] == "project:strongclaw"
    assert payload["includeDaily"] is False
    assert payload["memories"]
    assert {
        "fact",
        "preference",
        "entity",
        "other",
    }.issubset({entry["category"] for entry in payload["memories"]})
    assert all(
        entry["metadata"]["hypermemory"]["sourcePath"] != "memory/2026-03-16.md"
        for entry in payload["memories"]
    )


def test_hypermemory_export_memory_pro_can_include_daily_retained_notes(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))

    payload = engine.export_memory_pro_import(
        scope="project:strongclaw",
        include_daily=True,
    )

    daily_entries = [
        entry
        for entry in payload["memories"]
        if entry["metadata"]["hypermemory"]["sourcePath"] == "memory/2026-03-16.md"
    ]
    assert daily_entries
    assert any(entry["category"] == "preference" for entry in daily_entries)
    assert all(entry["id"].startswith("strongclaw-hypermemory:") for entry in daily_entries)


def test_hypermemory_export_memory_pro_includes_structured_provenance(
    tmp_path: pathlib.Path,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    (workspace / "MEMORY.md").write_text(
        """
        # Project Memory

        - Fact[evidence=docs/runbook.md#L1-L3|lcm://conversation/abc123/summary/sum_deadbeef]: Gateway rollout follows the runbook summary.
        """.strip() + "\n",
        encoding="utf-8",
    )
    engine = HypermemoryEngine(load_config(config_path))

    payload = engine.export_memory_pro_import(scope="project:strongclaw")

    structured_entry = next(
        entry
        for entry in payload["memories"]
        if "Gateway rollout follows the runbook summary." in entry["text"]
    )
    evidence = structured_entry["metadata"]["hypermemory"]["evidence"]
    assert {
        "kind": "file",
        "rel_path": "MEMORY.md",
        "start_line": 3,
        "end_line": 3,
        "relation": "supports",
    } in evidence
    assert {
        "kind": "file",
        "rel_path": "docs/runbook.md",
        "start_line": 1,
        "end_line": 3,
        "relation": "supports",
    } in evidence
    assert {
        "kind": "lcm_summary",
        "uri": "lcm://conversation/abc123/summary/sum_deadbeef",
        "relation": "supports",
    } in evidence


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


def test_hypermemory_get_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))

    assert engine.read("memory/2099-01-01.md") == {"path": "memory/2099-01-01.md", "text": ""}


def test_hypermemory_fact_registry_supersedes_and_exact_lookup(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(config, dedup=replace(config.dedup, enabled=True))
    engine = HypermemoryEngine(config)
    engine.reindex()

    first = engine.store(
        kind="fact",
        text="My timezone is UTC-3.",
        fact_key="user:timezone",
    )
    second = engine.store(
        kind="fact",
        text="My timezone is UTC+1.",
        fact_key="user:timezone",
    )

    assert first["stored"] is True
    assert second["superseded"] is True
    hit = engine.search("what is my timezone", lane="memory")[0]
    facts = engine.list_facts()
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert "UTC+1" in hit.snippet
    assert facts[0]["factKey"] == "user:timezone"
    assert "invalidated=" in world_text
    assert "supersedes=" in world_text


def test_hypermemory_forget_soft_delete_excludes_active_search(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()
    engine.store(kind="fact", text="Deploy freezes start every Friday at 17:00.")

    payload = engine.forget(entry_text="Deploy freezes start every Friday at 17:00.")
    active_hits = engine.search("deploy freezes start every friday", lane="memory")
    audit_hits = engine.search(
        "deploy freezes start every friday",
        lane="memory",
        include_invalidated=True,
    )

    assert payload["forgotten"] is True
    assert all(
        "Deploy freezes start every Friday at 17:00." not in hit.snippet for hit in active_hits
    )
    assert audit_hits
    assert audit_hits[0].invalidated_at is not None


def test_hypermemory_access_tracking_flushes_to_markdown(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()
    engine.store(kind="fact", text="Release approvals require the runbook link.")

    hit = engine.search("release approvals require runbook link", lane="memory")[0]
    payload = engine.record_access(item_ids=[hit.item_id or 0])
    flush_payload = engine.flush_metadata()
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert payload["updated"] == 1
    assert flush_payload["updatedEntries"] >= 1
    assert "accessed=1" in world_text
    assert "last_access=" in world_text


def test_hypermemory_capture_regex_stores_fact_keyed_memory(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(config, dedup=replace(config.dedup, enabled=True))
    engine = HypermemoryEngine(config)
    engine.reindex()

    payload = engine.capture(
        messages=[
            (0, "user", "Hello"),
            (1, "user", "My timezone is UTC-3"),
            (2, "user", "We decided to use PostgreSQL for our database"),
        ],
        mode="regex",
    )

    fact = engine.get_fact("user:timezone")

    assert payload["captured"] >= 1
    assert fact is not None
    assert "UTC-3" in fact.snippet


def test_hypermemory_feedback_counters_update_search_hits(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()
    engine.store(kind="fact", text="Browser lab access stays local-only.")

    hit = engine.search("browser lab access stays local only", lane="memory")[0]
    item_id = hit.item_id or 0
    engine.record_injection(item_ids=[item_id])
    engine.record_confirmation(item_ids=[item_id])
    engine.record_bad_recall(item_ids=[item_id])

    refreshed = engine.search("browser lab access stays local only", lane="memory")[0]

    assert refreshed.injected_count == 1
    assert refreshed.confirmed_count == 1
    assert refreshed.bad_recall_count == 1


def test_hypermemory_lifecycle_promotes_high_value_memory(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(config, decay=replace(config.decay, enabled=True))
    engine = HypermemoryEngine(config)
    engine.reindex()
    engine.store(
        kind="fact",
        text="The deployment checklist is the primary release gate.",
        importance=0.95,
    )

    with engine.connect() as conn:
        conn.execute("""
            UPDATE search_items
            SET access_count = 12, tier = 'working'
            WHERE snippet LIKE '%primary release gate%'
            """)
        conn.commit()

    payload = engine.run_lifecycle()
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert payload["changed"] >= 1
    assert "tier=core" in world_text
