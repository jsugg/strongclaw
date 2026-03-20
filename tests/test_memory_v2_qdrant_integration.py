"""Qdrant-backed integration tests for the memory-v2 dense path."""

from __future__ import annotations

import hashlib
import os
import pathlib
import textwrap
from dataclasses import replace
from typing import Any

import pytest
import requests

from clawops.memory_v2 import MemoryV2Engine, load_config

QDRANT_URL_ENV = "TEST_QDRANT_URL"


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
    (workspace / "docs" / "runbook.md").write_text(
        """
        # Gateway Runbook

        Rotate the gateway token before enabling a new browser profile.
        """.strip() + "\n",
        encoding="utf-8",
    )
    return workspace


class _DeterministicEmbeddingProvider:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        normalized = text.lower()
        vector = [
            (
                1.0
                if any(
                    token in normalized
                    for token in ("gateway", "token", "credential", "rollover", "runbook")
                )
                else 0.0
            ),
            (
                1.0
                if any(token in normalized for token in ("deploy", "playbook", "cutovers"))
                else 0.0
            ),
            1.0 if "alice" in normalized else 0.0,
        ]
        if sum(value * value for value in vector) == 0.0:
            vector = [0.01, 0.01, 0.01]
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector]


def _query_points(
    *,
    base_url: str,
    collection: str,
    vector: list[float],
    using: str = "dense",
) -> list[dict[str, Any]]:
    response = requests.post(
        f"{base_url.rstrip('/')}/collections/{collection}/points/query",
        json={
            "query": vector,
            "using": using,
            "limit": 8,
            "with_payload": True,
            "with_vector": False,
        },
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json().get("result")
    if isinstance(payload, dict):
        payload = payload.get("points")
    if not isinstance(payload, list):
        return []
    return [point for point in payload if isinstance(point, dict)]


def test_memory_v2_qdrant_reindex_search_and_prune(tmp_path: pathlib.Path) -> None:
    qdrant_url = os.environ.get(QDRANT_URL_ENV)
    if not qdrant_url:
        pytest.skip(f"{QDRANT_URL_ENV} is not set")

    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    config = load_config(config_path)
    collection = f"memory-v2-int-{hashlib.sha1(tmp_path.as_posix().encode()).hexdigest()[:12]}"
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, url=qdrant_url, collection=collection),
    )

    engine = MemoryV2Engine(config)
    engine._embedding_provider = _DeterministicEmbeddingProvider()
    engine.reindex()

    status = engine.status()
    assert status["qdrantHealthy"] is True
    assert status["vectorItems"] >= 1

    hits = engine.search("credential rollover checklist", lane="all", auto_index=False)
    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert hits[0].backend == "qdrant_dense_hybrid"

    points_before = _query_points(
        base_url=qdrant_url,
        collection=collection,
        vector=[1.0, 0.0, 0.0],
        using=config.qdrant.dense_vector_name,
    )
    assert any(
        point.get("payload", {}).get("rel_path") == "docs/runbook.md" for point in points_before
    )

    (workspace / "docs" / "runbook.md").unlink()
    engine.reindex()

    points_after = _query_points(
        base_url=qdrant_url,
        collection=collection,
        vector=[1.0, 0.0, 0.0],
        using=config.qdrant.dense_vector_name,
    )
    assert all(
        point.get("payload", {}).get("rel_path") != "docs/runbook.md" for point in points_after
    )
