"""Tests for the hypermemory Qdrant backend."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest
import requests

from clawops.hypermemory.models import QdrantConfig
from clawops.hypermemory.qdrant_backend import QdrantBackend

pytestmark = pytest.mark.qdrant


class _FakeResponse:
    def __init__(self, payload: object | None = None, *, status_code: int = 200) -> None:
        self._payload: object = {} if payload is None else payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.conflict_on_collection = False
        self.put_outcomes: list[_FakeResponse | Exception] = []
        self.post_outcomes: list[_FakeResponse | Exception] = []
        self.get_outcomes: list[_FakeResponse | Exception] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self._resolve_outcome(
            self.get_outcomes,
            default=_FakeResponse(
                {
                    "result": {
                        "config": {
                            "params": {
                                "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
                                "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
                            }
                        }
                    }
                }
            ),
        )

    def put(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        headers: dict[str, str],
        timeout: float,
        params: Mapping[str, str] | None = None,
    ) -> _FakeResponse:
        self.put_calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "params": params,
            }
        )
        if self.conflict_on_collection and url.endswith("/collections/hypermemory-test"):
            return _FakeResponse(status_code=409)
        return self._resolve_outcome(self.put_outcomes, default=_FakeResponse())

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        headers: dict[str, str],
        timeout: float,
        params: Mapping[str, str] | None = None,
    ) -> _FakeResponse:
        self.post_calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "params": params,
            }
        )
        if url.endswith("/points/query"):
            return _FakeResponse(
                {
                    "result": [
                        {
                            "id": "point-1",
                            "score": 0.91,
                            "payload": {"item_id": 42, "scope": "project:strongclaw"},
                        }
                    ]
                }
            )
        return self._resolve_outcome(self.post_outcomes, default=_FakeResponse())

    def _resolve_outcome(
        self,
        outcomes: list[_FakeResponse | Exception],
        *,
        default: _FakeResponse,
    ) -> _FakeResponse:
        if not outcomes:
            return default
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_qdrant_backend_uses_expected_rest_payloads() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session

    backend.ensure_collection(vector_size=3, include_sparse=True)
    backend.upsert_points(
        [
            {
                "id": "point-1",
                "vector": {
                    "dense": [1.0, 0.0, 0.0],
                    "sparse": {"indices": [0, 3], "values": [0.7, 0.2]},
                },
                "payload": {
                    "item_id": 42,
                    "rel_path": "docs/runbook.md",
                    "lane": "corpus",
                    "source_name": "docs",
                    "item_type": "paragraph",
                    "scope": "project:strongclaw",
                    "start_line": 1,
                    "end_line": 1,
                    "modified_at": "2026-03-26T00:00:00+00:00",
                    "confidence": None,
                },
            }
        ]
    )
    hits = backend.search_dense(
        vector=[1.0, 0.0, 0.0], limit=5, mode="memory", scope="project:strongclaw"
    )
    create_payload = cast(dict[str, object], fake_session.put_calls[0]["json"])
    upsert_payload = cast(dict[str, object], fake_session.put_calls[1]["json"])
    search_payload = cast(dict[str, object], fake_session.post_calls[-1]["json"])
    vectors = cast(dict[str, object], create_payload["vectors"])
    dense_vector = cast(dict[str, object], vectors["dense"])
    sparse_vectors = cast(dict[str, object], create_payload["sparse_vectors"])
    sparse_lane = cast(dict[str, object], sparse_vectors["sparse"])
    sparse_index = cast(dict[str, object], sparse_lane["index"])
    points = cast(list[object], upsert_payload["points"])
    first_point = cast(dict[str, object], points[0])
    point_vectors = cast(dict[str, object], first_point["vector"])
    search_filter = cast(dict[str, object], search_payload["filter"])
    must_filters = cast(list[object], search_filter["must"])
    first_filter = cast(dict[str, object], must_filters[0])

    assert cast(str, fake_session.put_calls[0]["url"]).endswith("/collections/hypermemory-test")
    assert dense_vector["size"] == 3
    assert sparse_index["on_disk"] is False
    assert point_vectors["dense"] == [1.0, 0.0, 0.0]
    assert cast(dict[str, object], point_vectors["sparse"])["indices"] == [0, 3]
    assert cast(str, fake_session.post_calls[-1]["url"]).endswith("/points/query")
    assert search_payload["using"] == "dense"
    assert first_filter["key"] == "lane"
    assert hits[0].item_id == 42
    assert hits[0].point_id == "point-1"


def test_qdrant_backend_treats_existing_collection_as_idempotent() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session
    fake_session.conflict_on_collection = True

    backend.ensure_collection(vector_size=3)

    assert cast(str, fake_session.put_calls[0]["url"]).endswith("/collections/hypermemory-test")


def test_qdrant_backend_retries_collection_creation_until_ready() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session
    fake_session.put_outcomes = [
        requests.ReadTimeout("timed out"),
        _FakeResponse(),
    ]
    fake_session.get_outcomes = [
        _FakeResponse(status_code=404),
        _FakeResponse(
            {
                "result": {
                    "config": {
                        "params": {
                            "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
                            "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
                        }
                    }
                }
            }
        ),
    ]

    backend.ensure_collection(vector_size=3, include_sparse=True)

    assert len(fake_session.put_calls) == 2
    assert len(fake_session.get_calls) == 2
    assert fake_session.put_calls[0]["params"] == {"timeout": "10"}


def test_qdrant_backend_health_prefers_readyz_and_falls_back_to_healthz() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session
    fake_session.get_outcomes = [
        _FakeResponse(status_code=404),
        _FakeResponse(),
    ]

    health = backend.health()

    assert health["healthy"] is True
    assert health["probe"] == "healthz"
    assert cast(str, fake_session.get_calls[0]["url"]).endswith("/readyz")
    assert cast(str, fake_session.get_calls[1]["url"]).endswith("/healthz")


def test_qdrant_backend_shapes_sparse_query_payloads() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session

    hits = backend.search_sparse(
        vector={"indices": [1, 4], "values": [0.9, 0.4]},
        limit=4,
        mode="all",
        scope=None,
    )
    search_payload = cast(dict[str, object], fake_session.post_calls[-1]["json"])

    assert search_payload["query"] == {
        "indices": [1, 4],
        "values": [0.9, 0.4],
    }
    assert search_payload["using"] == "sparse"
    assert hits[0].item_id == 42


def test_qdrant_backend_retries_point_upserts_after_transient_server_errors() -> None:
    fake_session = _FakeSession()
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    cast(Any, backend)._session = fake_session
    fake_session.put_outcomes = [
        _FakeResponse(status_code=500),
        _FakeResponse(),
    ]

    backend.upsert_points(
        [
            {
                "id": "point-1",
                "vector": {"dense": [1.0, 0.0, 0.0]},
                "payload": {
                    "item_id": 42,
                    "rel_path": "docs/runbook.md",
                    "lane": "corpus",
                    "source_name": "docs",
                    "item_type": "paragraph",
                    "scope": "project:strongclaw",
                    "start_line": 1,
                    "end_line": 1,
                    "modified_at": "2026-03-26T00:00:00+00:00",
                    "confidence": None,
                },
            }
        ]
    )

    assert len(fake_session.put_calls) == 2
