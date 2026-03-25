"""CLI and config-surface tests for the StrongClaw hypermemory engine."""

from __future__ import annotations

import json
import pathlib
import textwrap

import pytest

from clawops.hypermemory import load_config, main
from tests.fixtures.hypermemory import build_workspace, write_hypermemory_config


def test_hypermemory_load_config_resolves_required_env_backed_strings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory-env.yaml"
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
              paths: []
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            backend:
              active: qdrant_sparse_dense_hybrid
            embedding:
              enabled: true
              provider: compatible-http
              model: os.environ/TEST_HYPERMEMORY_EMBED_MODEL
              base_url: os.environ/TEST_HYPERMEMORY_EMBED_URL
            qdrant:
              enabled: true
              url: os.environ/TEST_HYPERMEMORY_QDRANT_URL
            """).strip() + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_HYPERMEMORY_EMBED_MODEL", "hypermemory-embedding")
    monkeypatch.setenv("TEST_HYPERMEMORY_EMBED_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("TEST_HYPERMEMORY_QDRANT_URL", "http://127.0.0.1:6333")

    config = load_config(config_path)

    assert config.embedding.model == "hypermemory-embedding"
    assert config.embedding.base_url == "http://127.0.0.1:4000/v1"
    assert config.qdrant.url == "http://127.0.0.1:6333"


def test_hypermemory_load_config_rejects_missing_required_env_backed_strings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory-env.yaml"
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: os.environ/TEST_HYPERMEMORY_WORKSPACE_ROOT
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: os.environ/TEST_HYPERMEMORY_CORPUS_PATH
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TEST_HYPERMEMORY_CORPUS_PATH", raising=False)

    with pytest.raises(TypeError, match="corpus.paths\\[0\\]\\.path"):
        load_config(config_path)


def test_hypermemory_cli_search_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

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


def test_hypermemory_cli_benchmark_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
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


def test_hypermemory_cli_export_memory_pro_writes_import_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
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
