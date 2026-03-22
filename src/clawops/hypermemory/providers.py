"""Inference provider adapters for StrongClaw hypermemory."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

import requests

from clawops.hypermemory.models import EmbeddingConfig, RerankConfig


class EmbeddingProvider(Protocol):
    """Contract for embedding text batches."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...


class RerankProvider(Protocol):
    """Contract for reranking scored results."""

    def rerank(
        self, query: str, candidates: Sequence[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Return candidates in reranked order."""
        ...


class DisabledEmbeddingProvider:
    """Provider used when dense retrieval is disabled."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Reject embedding calls when the feature is disabled."""
        raise RuntimeError("embedding provider is disabled")


class CompatibleHttpEmbeddingProvider:
    """Embedding provider for compatible HTTP endpoints."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._session = requests.Session()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed *texts* through the configured HTTP endpoint."""
        if not self._config.enabled:
            raise RuntimeError("embedding provider is disabled")
        if self._config.provider != "compatible-http":
            raise RuntimeError(f"unsupported embedding provider: {self._config.provider}")
        if not self._config.base_url:
            raise ValueError("embedding.base_url is required when embeddings are enabled")
        if not self._config.model:
            raise ValueError("embedding.model is required when embeddings are enabled")
        payload = {"input": list(texts), "model": self._config.model}
        response = self._session.post(
            f"{self._config.base_url.rstrip('/')}/embeddings",
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        body = response.json()
        raw_data = body.get("data")
        if not isinstance(raw_data, list):
            raise ValueError("embedding response is missing a data list")
        ordered = sorted(raw_data, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for index, item in enumerate(ordered):
            if not isinstance(item, dict):
                raise TypeError(f"embedding response item {index} must be a mapping")
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list) or not raw_vector:
                raise TypeError(f"embedding response item {index} is missing an embedding vector")
            vector = [float(value) for value in raw_vector]
            if self._config.dimensions is not None and len(vector) != self._config.dimensions:
                raise ValueError(
                    f"embedding vector dimension {len(vector)} does not match configured "
                    f"dimensions {self._config.dimensions}"
                )
            vectors.append(_normalize_vector(vector))
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match the request size")
        return vectors

    def _headers(self) -> dict[str, str]:
        """Return request headers for the embedding call."""
        headers = {"Content-Type": "application/json"}
        api_key = _resolve_api_key(
            api_key_env=self._config.api_key_env, api_key=self._config.api_key
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers


class NoopRerankProvider:
    """Rerank provider used when reranking is disabled."""

    def rerank(
        self, query: str, candidates: Sequence[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Return candidates unchanged."""
        del query
        return list(candidates)


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Create an embedding provider for *config*."""
    if not config.enabled or config.provider == "disabled":
        return DisabledEmbeddingProvider()
    if config.provider == "compatible-http":
        return CompatibleHttpEmbeddingProvider(config)
    raise RuntimeError(f"unsupported embedding provider: {config.provider}")


def create_rerank_provider(config: RerankConfig) -> RerankProvider:
    """Create a rerank provider for *config*."""
    if not config.enabled or config.provider == "none":
        return NoopRerankProvider()
    raise RuntimeError(f"unsupported rerank provider: {config.provider}")


def _resolve_api_key(*, api_key_env: str | None, api_key: str | None) -> str | None:
    """Return the configured API key value, preferring the environment."""
    if api_key_env:
        resolved = os.environ.get(api_key_env)
        if resolved:
            return resolved
    if api_key:
        return api_key
    return None


def _normalize_vector(vector: Sequence[float]) -> list[float]:
    """Normalize a vector to unit length for cosine search."""
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0.0:
        raise ValueError("embedding vector norm must be positive")
    return [value / norm for value in vector]
