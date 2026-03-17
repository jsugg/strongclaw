"""Tests for the strongclaw memory v2 engine."""

from __future__ import annotations

import json
import pathlib
import textwrap

import pytest

from clawops.memory_v2 import MemoryV2Engine, default_config_path, load_config, main


def _write_memory_v2_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-memory-v2.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
                - memory.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )


def _build_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "memory").mkdir(parents=True)
    (workspace / "bank").mkdir(parents=True)
    (workspace / "MEMORY.md").write_text(
        "# Project Memory\n\n- Fact: The deploy process uses blue/green cutovers.\n",
        encoding="utf-8",
    )
    (workspace / "memory" / "2026-03-16.md").write_text(
        """
        # Daily Log

        ## Retain
        - Fact: Alice owns the deployment playbook.
        - Opinion[c=0.90]: QMD improves recall but should surface degraded mode.
        - Entity[Alice]: Maintains the gateway rollout checklist.
        """.strip() + "\n",
        encoding="utf-8",
    )
    (workspace / "docs" / "runbook.md").write_text(
        """
        # Gateway Runbook

        Rotate the gateway token before enabling a new browser profile.
        """.strip() + "\n",
        encoding="utf-8",
    )
    return workspace


def test_load_shipped_memory_v2_config() -> None:
    config = load_config(default_config_path())
    assert config.include_default_memory is True
    assert config.db_path.name == "memory-v2.sqlite"
    assert any(entry.name == "runbooks" for entry in config.corpus_paths)


def test_memory_v2_reindex_and_search(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)

    engine = MemoryV2Engine(load_config(config_path))
    summary = engine.reindex()

    assert summary.files >= 3
    assert summary.chunks >= 3

    hits = engine.search("gateway token", lane="all")
    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert "Rotate the gateway token" in hits[0].snippet


def test_memory_v2_store_update_and_reflect(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))
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


def test_memory_v2_scope_filter_and_explain(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))
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


def test_memory_v2_reflect_global_scope_becomes_pending_proposal(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    (workspace / "memory" / "2026-03-17.md").write_text(
        """
        # Daily Log

        ## Retain
        - Fact[scope=global]: Shared browser lab access remains disabled.
        """.strip() + "\n",
        encoding="utf-8",
    )
    engine = MemoryV2Engine(load_config(config_path))
    engine.reindex()

    payload = engine.reflect(mode="safe")
    proposals_text = (workspace / "bank" / "proposals.md").read_text(encoding="utf-8")
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert payload["pending"] >= 1
    assert "scope=global" in proposals_text
    assert "Shared browser lab access remains disabled." not in world_text


def test_memory_v2_benchmark_runner(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))
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

    assert payload["provider"] == "strongclaw-memory-v2"
    assert payload["passed"] == 1
    assert payload["cases"][0]["passed"] is True


def test_memory_v2_export_memory_pro_defaults_to_durable_surfaces(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))
    engine.reindex()
    engine.reflect()
    engine.store(
        kind="reflection",
        text="Prefer canary rollouts for gateway migrations.",
        scope="project:strongclaw",
    )

    payload = engine.export_memory_pro_import(scope="project:strongclaw")

    assert payload["provider"] == "strongclaw-memory-v2"
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
        entry["metadata"]["memoryV2"]["sourcePath"] != "memory/2026-03-16.md"
        for entry in payload["memories"]
    )


def test_memory_v2_export_memory_pro_can_include_daily_retained_notes(
    tmp_path: pathlib.Path,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))

    payload = engine.export_memory_pro_import(
        scope="project:strongclaw",
        include_daily=True,
    )

    daily_entries = [
        entry
        for entry in payload["memories"]
        if entry["metadata"]["memoryV2"]["sourcePath"] == "memory/2026-03-16.md"
    ]
    assert daily_entries
    assert any(entry["category"] == "preference" for entry in daily_entries)
    assert all(entry["id"].startswith("strongclaw-memory-v2:") for entry in daily_entries)


def test_memory_v2_get_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    engine = MemoryV2Engine(load_config(config_path))

    assert engine.read("memory/2099-01-01.md") == {"path": "memory/2099-01-01.md", "text": ""}


def test_memory_v2_cli_search_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "search",
            "--query",
            "deployment playbook",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["results"]
    assert payload["results"][0]["path"] in {"MEMORY.md", "memory/2026-03-16.md"}


def test_memory_v2_cli_benchmark_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    fixtures_path = workspace / "benchmark.yaml"
    fixtures_path.write_text(
        textwrap.dedent("""
            cases:
              - name: runbook
                query: gateway token
                lane: corpus
                expectedPaths:
                  - docs/runbook.md
            """).strip() + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "benchmark",
            "--fixtures",
            str(fixtures_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["passed"] == 1


def test_memory_v2_cli_export_memory_pro_writes_import_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    output_path = workspace / "memory-pro-import.json"

    exit_code = main(
        [
            "--config",
            str(config_path),
            "export-memory-pro",
            "--scope",
            "project:strongclaw",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert summary["memories"] == len(payload["memories"])
    assert summary["output"] == output_path.as_posix()
    assert summary["nextCommand"] == (
        f"openclaw memory-pro import {output_path.as_posix()} --scope project:strongclaw"
    )
    assert payload["scope"] == "project:strongclaw"
    assert payload["memories"]
