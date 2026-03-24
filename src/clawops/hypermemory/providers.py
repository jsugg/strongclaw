"""Inference provider adapters for StrongClaw hypermemory."""

from __future__ import annotations

import importlib
import math
import os
import platform
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

import requests

from clawops.hypermemory.models import (
    CompatibleHttpRerankConfig,
    EmbeddingConfig,
    LocalSentenceTransformersRerankConfig,
    RerankConfig,
    RerankProviderKind,
    RerankResponse,
)
from clawops.observability import emit_structured_log


class EmbeddingProvider(Protocol):
    """Contract for embedding text batches."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...


class RerankProvider(Protocol):
    """Contract for planner-stage rerank scoring."""

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        """Return one normalized score per document when reranking applies."""
        ...


class _RerankBackend(Protocol):
    """Provider-specific rerank backend."""

    kind: RerankProviderKind

    def score_documents(self, query: str, documents: Sequence[str]) -> list[float]:
        """Return one raw score per document."""
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


class LocalSentenceTransformersRerankProvider:
    """Local rerank backend powered by sentence-transformers CrossEncoder."""

    kind: RerankProviderKind = "local-sentence-transformers"

    def __init__(self, config: LocalSentenceTransformersRerankConfig) -> None:
        self._config = config
        self._model: object | None = None
        self._resolved_device: str | None = None

    def score_documents(self, query: str, documents: Sequence[str]) -> list[float]:
        """Score *documents* with the configured local cross-encoder."""
        if not self._config.model:
            raise ValueError(
                "rerank.local.model is required for the local sentence-transformers provider"
            )
        if not documents:
            return []
        try:
            model = self._load_model()
            raw_scores = self._predict(model, query=query, documents=documents)
        except Exception as err:
            current_device = self.resolved_device()
            if not self._should_fallback_to_cpu(current_device):
                raise
            emit_structured_log(
                "clawops.hypermemory.rerank.local.device_fallback",
                {
                    "configuredDevice": self._config.device,
                    "resolvedDevice": current_device,
                    "fallbackDevice": "cpu",
                    "error": str(err),
                },
            )
            model = self._load_model(device="cpu", force_reload=True)
            raw_scores = self._predict(model, query=query, documents=documents)
        return _coerce_score_sequence(
            raw_scores,
            expected_count=len(documents),
            source="local sentence-transformers rerank response",
        )

    def resolved_device(self) -> str:
        """Return the selected runtime device for the local reranker."""
        if self._resolved_device is not None:
            return self._resolved_device
        if self._config.device and self._config.device != "auto":
            return self._config.device
        try:
            module = importlib.import_module("torch")
        except ImportError:
            return "cpu"
        cuda = getattr(module, "cuda", None)
        if (
            cuda is not None
            and callable(getattr(cuda, "is_available", None))
            and cuda.is_available()
        ):
            return "cuda"
        if platform.system().casefold() == "darwin" and platform.machine().casefold() in {
            "arm64",
            "aarch64",
        }:
            backends = getattr(module, "backends", None)
            mps = getattr(backends, "mps", None)
            is_available = getattr(mps, "is_available", None)
            is_built = getattr(mps, "is_built", None)
            if callable(is_available) and is_available():
                if not callable(is_built) or is_built():
                    return "mps"
        return "cpu"

    def _predict(self, model: object, *, query: str, documents: Sequence[str]) -> object:
        """Run one prediction batch through the loaded cross-encoder."""
        predict = getattr(model, "predict", None)
        if not callable(predict):
            raise RuntimeError("sentence-transformers CrossEncoder is missing predict()")
        pairs = [(query, document) for document in documents]
        try:
            return predict(
                pairs,
                batch_size=self._config.batch_size,
                show_progress_bar=False,
            )
        except TypeError:
            return predict(pairs, batch_size=self._config.batch_size)

    def _should_fallback_to_cpu(self, device: str) -> bool:
        """Return whether automatic device selection should retry on CPU."""
        return device != "cpu" and (not self._config.device or self._config.device == "auto")

    def _load_model(self, *, device: str | None = None, force_reload: bool = False) -> object:
        """Load and cache the configured CrossEncoder instance."""
        resolved_device = self.resolved_device() if device is None else device
        if (
            not force_reload
            and self._model is not None
            and self._resolved_device == resolved_device
        ):
            return self._model
        try:
            module = importlib.import_module("sentence_transformers")
        except ImportError as err:
            raise RuntimeError(
                "local reranking requires sentence-transformers on a supported "
                "host/Python combination; otherwise configure "
                "rerank.provider=compatible-http"
            ) from err
        cross_encoder_cls = getattr(module, "CrossEncoder", None)
        if cross_encoder_cls is None:
            raise RuntimeError("sentence-transformers is missing CrossEncoder")
        init_kwargs: dict[str, object] = {"max_length": self._config.max_length}
        if resolved_device:
            init_kwargs["device"] = resolved_device
        try:
            self._model = cross_encoder_cls(self._config.model, **init_kwargs)
        except TypeError:
            fallback_kwargs = {
                name: value for name, value in init_kwargs.items() if name != "max_length"
            }
            if fallback_kwargs:
                try:
                    self._model = cross_encoder_cls(self._config.model, **fallback_kwargs)
                except TypeError:
                    self._model = cross_encoder_cls(self._config.model)
            else:
                self._model = cross_encoder_cls(self._config.model)
        self._resolved_device = resolved_device
        return self._model


class CompatibleHttpRerankProvider:
    """Compatible HTTP rerank backend for local or remote rerank endpoints."""

    kind: RerankProviderKind = "compatible-http"

    def __init__(self, config: CompatibleHttpRerankConfig) -> None:
        self._config = config
        self._session = requests.Session()

    def score_documents(self, query: str, documents: Sequence[str]) -> list[float]:
        """Score *documents* through the configured compatible HTTP endpoint."""
        if not self._config.base_url:
            raise ValueError(
                "rerank.compatible_http.base_url is required for the compatible-http provider"
            )
        if not documents:
            return []
        payload: dict[str, object] = {"query": query, "texts": list(documents)}
        if self._config.model:
            payload["model"] = self._config.model
        response = self._session.post(
            _resolve_rerank_endpoint(self._config.base_url),
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        return _parse_compatible_http_scores(
            response.json(),
            expected_count=len(documents),
        )

    def _headers(self) -> dict[str, str]:
        """Return request headers for the rerank call."""
        headers = {"Content-Type": "application/json"}
        api_key = _resolve_api_key(
            api_key_env=self._config.api_key_env,
            api_key=self._config.api_key,
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers


class NoopRerankProvider:
    """Rerank provider used when reranking is disabled or unconfigured."""

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        """Return a no-op rerank result."""
        del query, documents
        return RerankResponse()

    def resolved_device(self) -> str:
        """Return no runtime device because reranking is disabled."""
        return ""


class ConfiguredRerankProvider:
    """Primary/fallback rerank provider chain."""

    def __init__(self, config: RerankConfig) -> None:
        self._config = config
        self._backends: dict[RerankProviderKind, _RerankBackend] = {}

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        """Score *documents* through the configured primary/fallback chain."""
        provider_chain = _rerank_provider_chain(self._config)
        if not documents or not provider_chain:
            return RerankResponse()
        errors: list[str] = []
        for index, provider_kind in enumerate(provider_chain):
            backend = self._backend(provider_kind)
            try:
                raw_scores = backend.score_documents(query, documents)
            except Exception as err:
                errors.append(f"{provider_kind}: {err}")
                continue
            scores = (
                _normalize_rerank_scores(raw_scores)
                if self._config.normalize_scores
                else [float(score) for score in raw_scores]
            )
            return RerankResponse(
                scores=tuple(scores),
                provider=provider_kind,
                applied=True,
                fallback_used=index > 0,
            )
        raise RuntimeError("; ".join(errors) or "no rerank providers are configured")

    def _backend(self, provider_kind: RerankProviderKind) -> _RerankBackend:
        """Return the cached backend for *provider_kind*."""
        backend = self._backends.get(provider_kind)
        if backend is not None:
            return backend
        if provider_kind == "local-sentence-transformers":
            backend = LocalSentenceTransformersRerankProvider(self._config.local)
        elif provider_kind == "compatible-http":
            backend = CompatibleHttpRerankProvider(self._config.compatible_http)
        else:
            raise RuntimeError(f"unsupported rerank provider: {provider_kind}")
        self._backends[provider_kind] = backend
        return backend

    def resolved_device(self) -> str:
        """Return the selected runtime device for the primary rerank backend."""
        provider_chain = _rerank_provider_chain(self._config)
        if not provider_chain or provider_chain[0] != "local-sentence-transformers":
            return ""
        backend = self._backend(provider_chain[0])
        resolver = getattr(backend, "resolved_device", None)
        if callable(resolver):
            return cast(str, resolver())
        return ""


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Create an embedding provider for *config*."""
    if not config.enabled or config.provider == "disabled":
        return DisabledEmbeddingProvider()
    if config.provider == "compatible-http":
        return CompatibleHttpEmbeddingProvider(config)
    raise RuntimeError(f"unsupported embedding provider: {config.provider}")


def create_rerank_provider(config: RerankConfig) -> RerankProvider:
    """Create a rerank provider for *config*."""
    if not config.enabled or not _rerank_provider_chain(config):
        return NoopRerankProvider()
    return ConfiguredRerankProvider(config)


def _resolve_api_key(*, api_key_env: str | None, api_key: str | None) -> str | None:
    """Return the configured API key value, preferring the environment."""
    if api_key_env:
        resolved = os.environ.get(api_key_env)
        if resolved:
            return resolved
    if api_key:
        return api_key
    return None


def _rerank_provider_chain(config: RerankConfig) -> tuple[RerankProviderKind, ...]:
    """Return the unique configured rerank providers in resolution order."""
    chain: list[RerankProviderKind] = []
    for provider_kind in (config.provider, config.fallback_provider):
        if provider_kind == "none" or provider_kind in chain:
            continue
        chain.append(provider_kind)
    return tuple(chain)


def _resolve_rerank_endpoint(base_url: str) -> str:
    """Return the rerank endpoint URL for *base_url*."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/rerank"):
        return normalized
    return f"{normalized}/rerank"


def _parse_compatible_http_scores(
    body: object,
    *,
    expected_count: int,
) -> list[float]:
    """Parse a compatible HTTP rerank response into one score per document."""
    items = _response_items(body)
    scores_by_index: dict[int, float] = {}
    for offset, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TypeError(f"rerank response item {offset} must be a mapping")
        raw_index = item.get("index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise TypeError(f"rerank response item {offset} is missing an integer index")
        raw_score = item.get("score", item.get("relevance_score"))
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise TypeError(f"rerank response item {offset} is missing a numeric score")
        if raw_index < 0 or raw_index >= expected_count:
            raise ValueError(f"rerank response index {raw_index} is outside the request range")
        scores_by_index[raw_index] = float(raw_score)
    if len(scores_by_index) != expected_count:
        raise ValueError("rerank response count does not match the request size")
    return [scores_by_index[index] for index in range(expected_count)]


def _response_items(body: object) -> list[object]:
    """Return the list-like rerank result payload from *body*."""
    if isinstance(body, list):
        return list(body)
    if isinstance(body, Mapping):
        for key in ("results", "data"):
            candidate = body.get(key)
            if isinstance(candidate, list):
                return list(candidate)
    raise TypeError("rerank response is missing a results list")


def _coerce_score_sequence(
    raw_scores: object,
    *,
    expected_count: int,
    source: str,
) -> list[float]:
    """Normalize one score payload into a list of floats."""
    if hasattr(raw_scores, "tolist"):
        raw_scores = cast(Any, raw_scores).tolist()
    if isinstance(raw_scores, (str, bytes)) or not isinstance(raw_scores, Sequence):
        raise TypeError(f"{source} must be a sequence of numeric scores")
    scores: list[float] = []
    for index, value in enumerate(raw_scores):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{source} item {index} must be numeric")
        scores.append(float(value))
    if len(scores) != expected_count:
        raise ValueError(f"{source} count does not match the request size")
    return scores


def _normalize_rerank_scores(scores: Sequence[float]) -> list[float]:
    """Normalize rerank scores into the closed unit interval."""
    if not scores:
        return []
    min_score = min(scores)
    max_score = max(scores)
    if min_score >= 0.0 and max_score <= 1.0:
        return [min(max(score, 0.0), 1.0) for score in scores]
    spread = max_score - min_score
    if spread > 1e-9:
        return [(score - min_score) / spread for score in scores]
    return [_sigmoid(score) for score in scores]


def _sigmoid(value: float) -> float:
    """Return a numerically stable sigmoid for *value*."""
    clamped = max(min(value, 60.0), -60.0)
    return 1.0 / (1.0 + math.exp(-clamped))


def _normalize_vector(vector: Sequence[float]) -> list[float]:
    """Normalize a vector to unit length for cosine search."""
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0.0:
        raise ValueError("embedding vector norm must be positive")
    return [value / norm for value in vector]
