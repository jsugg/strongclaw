"""Tests for the memory-v2 Qdrant backend."""

from __future__ import annotations

from typing import Any

from clawops.memory_v2.models import QdrantConfig
from clawops.memory_v2.qdrant_backend import QdrantBackend


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

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

    backend.ensure_collection(vector_size=3)
    backend.upsert_points(
        [{"id": "point-1", "vector": [1.0, 0.0, 0.0], "payload": {"item_id": 42}}]
    )
    hits = backend.search(
        vector=[1.0, 0.0, 0.0], limit=5, mode="memory", scope="project:strongclaw"
    )

    assert fake_session.put_calls[0]["url"].endswith("/collections/memory-v2-test")
    assert fake_session.put_calls[0]["json"]["vectors"]["size"] == 3
    assert fake_session.post_calls[-1]["url"].endswith("/points/query")
    assert fake_session.post_calls[-1]["json"]["filter"]["must"][0]["key"] == "lane"
    assert hits[0].item_id == 42
    assert hits[0].point_id == "point-1"
