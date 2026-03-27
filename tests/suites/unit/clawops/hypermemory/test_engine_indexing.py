"""Indexing and config coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
import textwrap

from clawops.hypermemory import HypermemoryEngine, default_config_path, load_config
from clawops.hypermemory.config import matches_glob
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


def test_matches_glob_respects_repo_path_segments() -> None:
    assert matches_glob("README.md", "*.md") is True
    assert matches_glob("platform/docs/DEVFLOW.md", "*.md") is False
    assert matches_glob("platform/docs/DEVFLOW.md", "**/*.md") is True
    assert matches_glob("platform/docs/DEVFLOW.md", "platform/docs/*.md") is True
    assert matches_glob("platform/docs/runbooks/browser.md", "platform/docs/*.md") is False
    assert matches_glob("platform/docs/runbooks/browser.md", "platform/docs/**/*.md") is True


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


def test_hypermemory_reindex_deduplicates_overlapping_corpus_sources(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "platform" / "docs").mkdir(parents=True)
    (workspace / "platform" / "docs" / "DEVFLOW.md").write_text("# Devflow\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Repo\n", encoding="utf-8")
    config_path = workspace / "hypermemory-overlap.yaml"
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: .
              include_default_memory: false
              memory_file_names:
                - MEMORY.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: platform/docs
                  pattern: "**/*.md"
                - name: repo
                  path: .
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )

    engine = HypermemoryEngine(load_config(config_path))

    summary = engine.reindex()

    with engine.connect() as conn:
        rows = conn.execute(
            "SELECT rel_path, source_name FROM documents ORDER BY rel_path"
        ).fetchall()

    assert summary.files == 2
    assert [str(row["rel_path"]) for row in rows] == ["README.md", "platform/docs/DEVFLOW.md"]
    assert str(rows[1]["source_name"]) == "docs"


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
