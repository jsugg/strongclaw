"""Qdrant-backed integration tests for the hypermemory dense and sparse+dense paths."""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import socket
import subprocess
import textwrap
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
import requests

from clawops.hypermemory import HypermemoryEngine, load_config

QDRANT_URL_ENV = "TEST_QDRANT_URL"


def _wait_for_qdrant(url: str) -> None:
    """Wait for a Qdrant HTTP endpoint to report healthy."""
    last_error: Exception | None = None
    for _ in range(30):
        try:
            response = requests.get(f"{url.rstrip('/')}/healthz", timeout=1.0)
            response.raise_for_status()
            return
        except requests.RequestException as err:
            last_error = err
            time.sleep(1.0)
    detail = "unknown error" if last_error is None else str(last_error)
    raise RuntimeError(f"Qdrant did not become healthy at {url}: {detail}")


def _reserve_local_port() -> int:
    """Reserve an ephemeral localhost port for a temporary Qdrant container."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    """Provide a live Qdrant URL for the integration suite."""
    qdrant_url = os.environ.get(QDRANT_URL_ENV, "").strip()
    if qdrant_url:
        _wait_for_qdrant(qdrant_url)
        yield qdrant_url
        return

    docker_bin = shutil.which("docker")
    if docker_bin is None:
        pytest.fail(
            f"{QDRANT_URL_ENV} is unset and docker is unavailable; "
            "live Qdrant integration tests require a reachable Qdrant instance"
        )

    port = _reserve_local_port()
    container_name = f"strongclaw-qdrant-test-{uuid.uuid4().hex[:12]}"
    original_env = os.environ.get(QDRANT_URL_ENV)
    qdrant_url = f"http://127.0.0.1:{port}"
    try:
        result = subprocess.run(
            [
                docker_bin,
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{port}:6333",
                "qdrant/qdrant",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "docker run failed"
            pytest.fail(f"unable to start Qdrant test container: {detail}")
        os.environ[QDRANT_URL_ENV] = qdrant_url
        _wait_for_qdrant(qdrant_url)
        yield qdrant_url
    finally:
        if original_env is None:
            os.environ.pop(QDRANT_URL_ENV, None)
        else:
            os.environ[QDRANT_URL_ENV] = original_env
        subprocess.run(
            [docker_bin, "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
        )


def _write_hypermemory_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
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
    query: list[float] | dict[str, list[int] | list[float]],
    using: str = "dense",
) -> list[dict[str, Any]]:
    response = requests.post(
        f"{base_url.rstrip('/')}/collections/{collection}/points/query",
        json={
            "query": query,
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


def test_hypermemory_qdrant_reindex_search_and_prune(
    tmp_path: pathlib.Path, qdrant_url: str
) -> None:

    workspace = _build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    _write_hypermemory_config(workspace, config_path)
    config = load_config(config_path)
    collection = f"hypermemory-int-{hashlib.sha1(tmp_path.as_posix().encode()).hexdigest()[:12]}"
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

    engine = HypermemoryEngine(config)
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
        query=[1.0, 0.0, 0.0],
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
        query=[1.0, 0.0, 0.0],
        using=config.qdrant.dense_vector_name,
    )
    assert all(
        point.get("payload", {}).get("rel_path") != "docs/runbook.md" for point in points_after
    )


def test_hypermemory_qdrant_sparse_dense_backend_uses_qdrant_sparse_candidates(
    tmp_path: pathlib.Path,
    qdrant_url: str,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    _write_hypermemory_config(workspace, config_path)
    config = load_config(config_path)
    collection = f"hypermemory-sparse-{hashlib.sha1(tmp_path.as_posix().encode()).hexdigest()[:12]}"
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
        qdrant=replace(config.qdrant, enabled=True, url=qdrant_url, collection=collection),
    )

    engine = HypermemoryEngine(config)
    engine._embedding_provider = _DeterministicEmbeddingProvider()
    engine.reindex()

    collection_response = requests.get(
        f"{qdrant_url.rstrip('/')}/collections/{collection}",
        timeout=5.0,
    )
    collection_response.raise_for_status()
    params = collection_response.json()["result"]["config"]["params"]
    assert config.qdrant.dense_vector_name in params["vectors"]
    assert config.qdrant.sparse_vector_name in params["sparse_vectors"]

    with engine.connect() as conn:
        encoder = engine._load_sparse_encoder(conn)
        assert encoder is not None
        conn.execute("DELETE FROM search_items_fts")
        conn.commit()

    sparse_hits = _query_points(
        base_url=qdrant_url,
        collection=collection,
        query=encoder.encode_query("gateway token").to_qdrant(),
        using=config.qdrant.sparse_vector_name,
    )
    assert any(hit.get("payload", {}).get("rel_path") == "docs/runbook.md" for hit in sparse_hits)

    hits = engine.search("gateway token", lane="all", auto_index=False)
    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert hits[0].backend == "qdrant_sparse_dense_hybrid"

    verification = engine.verify()
    assert verification["ok"] is True
    assert verification["laneChecks"]["sparse"]["hits"] >= 1
