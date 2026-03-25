"""Memory-export coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib

from clawops.hypermemory import HypermemoryEngine, load_config
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config


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
