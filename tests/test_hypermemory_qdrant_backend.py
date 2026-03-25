"""Tests for the hypermemory Qdrant backend."""

from __future__ import annotations

from typing import Any

import requests

from clawops.hypermemory.models import QdrantConfig
from clawops.hypermemory.qdrant_backend import QdrantBackend


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, *, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
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
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
        params: dict[str, str] | None = None,
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
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
        params: dict[str, str] | None = None,
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
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    fake_session = _FakeSession()
    backend._session = fake_session  # type: ignore[assignment]

    backend.ensure_collection(vector_size=3, include_sparse=True)
    backend.upsert_points(
        [
            {
                "id": "point-1",
                "vector": {
                    "dense": [1.0, 0.0, 0.0],
                    "sparse": {"indices": [0, 3], "values": [0.7, 0.2]},
                },
                "payload": {"item_id": 42},
            }
        ]
    )
    hits = backend.search_dense(
        vector=[1.0, 0.0, 0.0], limit=5, mode="memory", scope="project:strongclaw"
    )

    assert fake_session.put_calls[0]["url"].endswith("/collections/hypermemory-test")
    assert fake_session.put_calls[0]["json"]["vectors"]["dense"]["size"] == 3
    assert (
        fake_session.put_calls[0]["json"]["sparse_vectors"]["sparse"]["index"]["on_disk"] is False
    )
    assert fake_session.put_calls[1]["json"]["points"][0]["vector"]["dense"] == [1.0, 0.0, 0.0]
    assert fake_session.put_calls[1]["json"]["points"][0]["vector"]["sparse"]["indices"] == [0, 3]
    assert fake_session.post_calls[-1]["url"].endswith("/points/query")
    assert fake_session.post_calls[-1]["json"]["using"] == "dense"
    assert fake_session.post_calls[-1]["json"]["filter"]["must"][0]["key"] == "lane"
    assert hits[0].item_id == 42
    assert hits[0].point_id == "point-1"


def test_qdrant_backend_treats_existing_collection_as_idempotent() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    fake_session = _FakeSession()
    fake_session.conflict_on_collection = True
    backend._session = fake_session  # type: ignore[assignment]

    backend.ensure_collection(vector_size=3)

    assert fake_session.put_calls[0]["url"].endswith("/collections/hypermemory-test")


def test_qdrant_backend_retries_collection_creation_until_ready() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    fake_session = _FakeSession()
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
    backend._session = fake_session  # type: ignore[assignment]

    backend.ensure_collection(vector_size=3, include_sparse=True)

    assert len(fake_session.put_calls) == 2
    assert len(fake_session.get_calls) == 2
    assert fake_session.put_calls[0]["params"] == {"timeout": "10"}


def test_qdrant_backend_health_prefers_readyz_and_falls_back_to_healthz() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    fake_session = _FakeSession()
    fake_session.get_outcomes = [
        _FakeResponse(status_code=404),
        _FakeResponse(),
    ]
    backend._session = fake_session  # type: ignore[assignment]

    health = backend.health()

    assert health["healthy"] is True
    assert health["probe"] == "healthz"
    assert fake_session.get_calls[0]["url"].endswith("/readyz")
    assert fake_session.get_calls[1]["url"].endswith("/healthz")


def test_qdrant_backend_shapes_sparse_query_payloads() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="hypermemory-test")
    )
    fake_session = _FakeSession()
    backend._session = fake_session  # type: ignore[assignment]

    hits = backend.search_sparse(
        vector={"indices": [1, 4], "values": [0.9, 0.4]},
        limit=4,
        mode="all",
        scope=None,
    )

    assert fake_session.post_calls[-1]["json"]["query"] == {
        "indices": [1, 4],
        "values": [0.9, 0.4],
    }
    assert fake_session.post_calls[-1]["json"]["using"] == "sparse"
    assert hits[0].item_id == 42
