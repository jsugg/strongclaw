"""Memory mutation coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, load_config
from tests.utils.helpers.hypermemory import (
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    build_workspace,
    write_hypermemory_config,
)


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


def test_hypermemory_store_defers_vector_sync_when_backend_fails(
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
    baseline_status = engine.status()
    fake_qdrant.raise_on_ensure_collection = True

    payload = engine.store(kind="fact", text="Blue/green deploys require approval windows.")
    hits = engine.search("approval windows", lane="memory")
    status = engine.status()

    assert payload["ok"] is True
    assert payload["stored"] is True
    assert payload["vectorSyncDeferred"] is True
    assert "qdrant collection warmup timed out" in payload["vectorSyncError"]
    assert any("approval windows" in hit.snippet.lower() for hit in hits)
    assert status["vectorSyncDeferred"] is True
    assert status["lastVectorSyncAt"] == baseline_status["lastVectorSyncAt"]
