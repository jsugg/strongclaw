"""Tests for the memory-v2 Qdrant backend."""

from __future__ import annotations

from typing import Any

from clawops.memory_v2.models import QdrantConfig
from clawops.memory_v2.qdrant_backend import QdrantBackend


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, *, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.conflict_on_collection = False

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers, "timeout": timeout})
        return _FakeResponse()

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
        if self.conflict_on_collection and url.endswith("/collections/memory-v2-test"):
            return _FakeResponse(status_code=409)
        return _FakeResponse()

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
        return _FakeResponse()


def test_qdrant_backend_uses_expected_rest_payloads() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="memory-v2-test")
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

    assert fake_session.put_calls[0]["url"].endswith("/collections/memory-v2-test")
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
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="memory-v2-test")
    )
    fake_session = _FakeSession()
    fake_session.conflict_on_collection = True
    backend._session = fake_session  # type: ignore[assignment]

    backend.ensure_collection(vector_size=3)

    assert fake_session.put_calls[0]["url"].endswith("/collections/memory-v2-test")


def test_qdrant_backend_shapes_sparse_query_payloads() -> None:
    backend = QdrantBackend(
        QdrantConfig(enabled=True, url="http://127.0.0.1:6333", collection="memory-v2-test")
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
