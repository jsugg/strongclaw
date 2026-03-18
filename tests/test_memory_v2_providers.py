"""Tests for memory-v2 inference providers."""

from __future__ import annotations

from typing import Any

from clawops.memory_v2.models import EmbeddingConfig
from clawops.memory_v2.providers import CompatibleHttpEmbeddingProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _FakeResponse(
            {
                "data": [
                    {"index": 0, "embedding": [3.0, 4.0]},
                    {"index": 1, "embedding": [5.0, 12.0]},
                ]
            }
        )


def test_compatible_http_embedding_provider_normalizes_vectors() -> None:
    config = EmbeddingConfig(
        enabled=True,
        provider="compatible-http",
        model="dense-test",
        base_url="http://127.0.0.1:4000/v1",
        api_key="local",
        dimensions=2,
    )
    provider = CompatibleHttpEmbeddingProvider(config)
    fake_session = _FakeSession()
    provider._session = fake_session  # type: ignore[assignment]

    vectors = provider.embed_texts(["first", "second"])

    assert fake_session.calls
    assert fake_session.calls[0]["url"] == "http://127.0.0.1:4000/v1/embeddings"
    assert fake_session.calls[0]["json"]["model"] == "dense-test"
    assert vectors[0] == [0.6, 0.8]
    assert vectors[1] == [5.0 / 13.0, 12.0 / 13.0]
