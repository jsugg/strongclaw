"""Tests for hypermemory inference providers."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from clawops.hypermemory.models import (
    CompatibleHttpRerankConfig,
    EmbeddingConfig,
    LocalSentenceTransformersRerankConfig,
    RerankConfig,
)
from clawops.hypermemory.providers import (
    CompatibleHttpEmbeddingProvider,
    CompatibleHttpRerankProvider,
    LocalSentenceTransformersRerankProvider,
    create_rerank_provider,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    def __init__(self, payload: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._payload = payload

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
        return _FakeResponse(self._payload)


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
    fake_session = _FakeSession(
        {
            "data": [
                {"index": 0, "embedding": [3.0, 4.0]},
                {"index": 1, "embedding": [5.0, 12.0]},
            ]
        }
    )
    provider._session = fake_session

    vectors = provider.embed_texts(["first", "second"])

    assert fake_session.calls
    assert fake_session.calls[0]["url"] == "http://127.0.0.1:4000/v1/embeddings"
    assert fake_session.calls[0]["json"]["model"] == "dense-test"
    assert vectors[0] == [0.6, 0.8]
    assert vectors[1] == [5.0 / 13.0, 12.0 / 13.0]


def test_compatible_http_rerank_provider_posts_texts_payload_and_preserves_order() -> None:
    config = CompatibleHttpRerankConfig(
        model="rerank-test",
        base_url="http://127.0.0.1:8081",
        api_key="local",
        timeout_ms=2_000,
    )
    provider = CompatibleHttpRerankProvider(config)
    fake_session = _FakeSession(
        {
            "results": [
                {"index": 1, "relevance_score": 0.25},
                {"index": 0, "relevance_score": 0.75},
            ]
        }
    )
    provider._session = fake_session

    scores = provider.score_documents("gateway token", ["alpha", "beta"])

    assert scores == [0.75, 0.25]
    assert fake_session.calls == [
        {
            "url": "http://127.0.0.1:8081/rerank",
            "json": {
                "query": "gateway token",
                "texts": ["alpha", "beta"],
                "model": "rerank-test",
            },
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer local",
            },
            "timeout": 2.0,
        }
    ]


def test_compatible_http_rerank_provider_accepts_tei_style_payload() -> None:
    config = CompatibleHttpRerankConfig(base_url="http://127.0.0.1:8081/rerank")
    provider = CompatibleHttpRerankProvider(config)
    fake_session = _FakeSession(
        [
            {"index": 0, "score": 0.99},
            {"index": 1, "score": 0.51},
        ]
    )
    provider._session = fake_session

    scores = provider.score_documents("gateway token", ["alpha", "beta"])

    assert scores == [0.99, 0.51]
    assert fake_session.calls[0]["url"] == "http://127.0.0.1:8081/rerank"


def test_local_sentence_transformers_rerank_provider_scores_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    original_import_module = importlib.import_module

    class _FakeCrossEncoder:
        def __init__(self, model: str, *, max_length: int) -> None:
            captured["model"] = model
            captured["max_length"] = max_length

        def predict(
            self,
            pairs: list[tuple[str, str]],
            *,
            batch_size: int,
            show_progress_bar: bool,
        ) -> list[float]:
            captured["pairs"] = pairs
            captured["batch_size"] = batch_size
            captured["show_progress_bar"] = show_progress_bar
            return [2.0, -1.0]

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(CrossEncoder=_FakeCrossEncoder)
            if name == "sentence_transformers"
            else original_import_module(name)
        ),
    )

    provider = LocalSentenceTransformersRerankProvider(
        LocalSentenceTransformersRerankConfig(
            model="BAAI/bge-reranker-v2-m3",
            batch_size=4,
            max_length=1_024,
        )
    )

    scores = provider.score_documents("gateway token", ["alpha", "beta"])

    assert scores == [2.0, -1.0]
    assert captured["model"] == "BAAI/bge-reranker-v2-m3"
    assert captured["max_length"] == 1_024
    assert captured["batch_size"] == 4
    assert captured["pairs"] == [("gateway token", "alpha"), ("gateway token", "beta")]


def test_rerank_provider_falls_back_to_compatible_http_when_local_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = _FakeSession(
        {
            "results": [
                {"index": 0, "score": 10.0},
                {"index": 1, "score": 5.0},
            ]
        }
    )
    monkeypatch.setattr("requests.Session", lambda: fake_session)

    original_import_module = importlib.import_module

    def _fake_import(name: str) -> Any:
        if name == "sentence_transformers":
            raise ImportError("missing optional dependency")
        return original_import_module(name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    provider = create_rerank_provider(
        RerankConfig(
            enabled=True,
            provider="local-sentence-transformers",
            fallback_provider="compatible-http",
            local=LocalSentenceTransformersRerankConfig(model="BAAI/bge-reranker-v2-m3"),
            compatible_http=CompatibleHttpRerankConfig(
                base_url="http://127.0.0.1:8081",
                timeout_ms=2_000,
            ),
        )
    )

    response = provider.score("gateway token", ["alpha", "beta"])

    assert response.applied is True
    assert response.provider == "compatible-http"
    assert response.fallback_used is True
    assert response.scores == (1.0, 0.0)


def test_rerank_provider_raises_when_no_backend_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import_module = importlib.import_module

    def _fake_import(name: str) -> Any:
        if name == "sentence_transformers":
            raise ImportError("missing optional dependency")
        return original_import_module(name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    provider = create_rerank_provider(
        RerankConfig(
            enabled=True,
            provider="local-sentence-transformers",
            fallback_provider="compatible-http",
            local=LocalSentenceTransformersRerankConfig(model="BAAI/bge-reranker-v2-m3"),
            compatible_http=CompatibleHttpRerankConfig(base_url=""),
        )
    )

    with pytest.raises(RuntimeError, match="local-sentence-transformers:"):
        provider.score("gateway token", ["alpha"])
