"""Qdrant-backed integration tests for the hypermemory dense and sparse+dense paths."""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

import pytest
import requests

from clawops.hypermemory import HypermemoryEngine, SearchBackend, load_config
from clawops.hypermemory.config import HypermemoryConfig
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config
from tests.utils.helpers.qdrant_runtime import QdrantRuntime

pytestmark = [pytest.mark.qdrant(mode="real"), pytest.mark.network_local]


class _DeterministicEmbeddingProvider:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
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
) -> list[Mapping[str, object]]:
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
    body = response.json()
    if not isinstance(body, Mapping):
        return []
    body_mapping = cast(Mapping[str, object], body)
    payload = body_mapping.get("result")
    if isinstance(payload, Mapping):
        payload = cast(Mapping[str, object], payload).get("points")
    if not isinstance(payload, list):
        return []
    return [
        cast(Mapping[str, object], point)
        for point in cast(list[object], payload)
        if isinstance(point, Mapping)
    ]


def _point_rel_path(point: Mapping[str, object]) -> str | None:
    """Return the indexed relative path from one Qdrant point payload."""
    payload = point.get("payload")
    if not isinstance(payload, Mapping):
        return None
    rel_path = cast(Mapping[str, object], payload).get("rel_path")
    return rel_path if isinstance(rel_path, str) else None


def _build_engine(
    tmp_path: pathlib.Path,
    qdrant_runtime: QdrantRuntime,
    *,
    active_backend: SearchBackend,
    collection_prefix: str,
) -> tuple[HypermemoryEngine, HypermemoryConfig, pathlib.Path, str, str]:
    workspace = build_workspace(tmp_path, include_daily_memory=False)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    config = load_config(config_path)
    qdrant_url = qdrant_runtime.require_live_url()
    collection = qdrant_runtime.prepare_collection(prefix=collection_prefix)
    config = replace(
        config,
        backend=replace(config.backend, active=active_backend, fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, url=qdrant_url, collection=collection),
    )
    return (
        HypermemoryEngine(config, embedding_provider=_DeterministicEmbeddingProvider()),
        config,
        workspace,
        qdrant_url,
        collection,
    )


def test_hypermemory_qdrant_reindex_search_and_prune(
    tmp_path: pathlib.Path,
    qdrant_runtime: QdrantRuntime,
) -> None:
    engine, config, workspace, qdrant_url, collection = _build_engine(
        tmp_path,
        qdrant_runtime,
        active_backend="qdrant_dense_hybrid",
        collection_prefix=f"hypermemory_int_{hashlib.sha1(tmp_path.as_posix().encode()).hexdigest()[:8]}",
    )
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
    assert any(_point_rel_path(point) == "docs/runbook.md" for point in points_before)

    (workspace / "docs" / "runbook.md").unlink()
    engine.reindex()

    points_after = _query_points(
        base_url=qdrant_url,
        collection=collection,
        query=[1.0, 0.0, 0.0],
        using=config.qdrant.dense_vector_name,
    )
    assert all(_point_rel_path(point) != "docs/runbook.md" for point in points_after)


def test_hypermemory_qdrant_sparse_dense_backend_uses_qdrant_sparse_candidates(
    tmp_path: pathlib.Path,
    qdrant_runtime: QdrantRuntime,
) -> None:
    engine, config, _workspace, qdrant_url, collection = _build_engine(
        tmp_path,
        qdrant_runtime,
        active_backend="qdrant_sparse_dense_hybrid",
        collection_prefix=(
            f"hypermemory_sparse_{hashlib.sha1(tmp_path.as_posix().encode()).hexdigest()[:8]}"
        ),
    )
    engine.reindex()

    collection_response = requests.get(
        f"{qdrant_url.rstrip('/')}/collections/{collection}",
        timeout=5.0,
    )
    collection_response.raise_for_status()
    collection_body = collection_response.json()
    if not isinstance(collection_body, Mapping):
        raise TypeError("collection response must be a mapping")
    collection_mapping = cast(Mapping[str, object], collection_body)
    result = collection_mapping.get("result")
    if not isinstance(result, Mapping):
        raise TypeError("collection response must include a result mapping")
    config_payload = cast(Mapping[str, object], result).get("config")
    if not isinstance(config_payload, Mapping):
        raise TypeError("collection response must include a config mapping")
    params = cast(Mapping[str, object], config_payload).get("params")
    if not isinstance(params, Mapping):
        raise TypeError("collection response must include params")
    vectors = cast(Mapping[str, object], params["vectors"])
    sparse_vectors = cast(Mapping[str, object], params["sparse_vectors"])
    assert config.qdrant.dense_vector_name in vectors
    assert config.qdrant.sparse_vector_name in sparse_vectors

    with engine.connect() as conn:
        encoder = engine.index.load_sparse_encoder(conn, enabled=True)
        assert encoder is not None
        conn.execute("DELETE FROM search_items_fts")
        conn.commit()

    sparse_hits = _query_points(
        base_url=qdrant_url,
        collection=collection,
        query=encoder.encode_query("gateway token").to_qdrant(),
        using=config.qdrant.sparse_vector_name,
    )
    assert any(_point_rel_path(hit) == "docs/runbook.md" for hit in sparse_hits)

    hits = engine.search("gateway token", lane="all", auto_index=False)
    assert hits
    assert hits[0].path == "docs/runbook.md"
    assert hits[0].backend == "qdrant_sparse_dense_hybrid"

    verification = engine.verify()
    sparse_lane = verification["laneChecks"].get("sparse")
    assert verification["ok"] is True
    assert sparse_lane is not None
    assert sparse_lane["hits"] >= 1
