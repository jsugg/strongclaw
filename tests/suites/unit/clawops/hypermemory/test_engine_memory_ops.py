"""Memory mutation and lifecycle coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, load_config
from tests.fixtures.hypermemory import build_workspace, write_hypermemory_config


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
