"""Qdrant dense and sparse retrieval backend for StrongClaw hypermemory."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar, cast

import requests

from clawops.hypermemory.contracts import SparseVectorPayload, VectorPoint
from clawops.hypermemory.models import (
    DenseSearchCandidate,
    QdrantConfig,
    SearchMode,
    SparseSearchCandidate,
)

_CandidateT = TypeVar("_CandidateT", DenseSearchCandidate, SparseSearchCandidate)
_COLLECTION_RETRY_ATTEMPTS = 4
_COLLECTION_READY_ATTEMPTS = 8
_POINTS_WRITE_RETRY_ATTEMPTS = 4
_POINTS_WRITE_BATCH_SIZE = 256


class VectorBackend(Protocol):
    """Contract for the dense/sparse vector retrieval backend.

    The hypermemory engine treats Qdrant as an optional sidecar dependency.
    This protocol exists to enable strict, test-friendly dependency injection
    while keeping the runtime implementation pluggable.
    """

    def health(self) -> dict[str, Any]:
        """Return a lightweight backend health payload."""
        ...

    def collection_details(self) -> dict[str, Any]:
        """Return live collection details when available."""
        ...

    def ensure_collection(self, *, vector_size: int, include_sparse: bool = False) -> None:
        """Create the collection when it does not exist."""
        ...

    def upsert_points(self, points: Sequence[VectorPoint]) -> None:
        """Upsert dense and sparse points into the configured collection."""
        ...

    def delete_points(self, point_ids: Sequence[str]) -> None:
        """Delete stale point IDs from the configured collection."""
        ...

    def search_dense(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[DenseSearchCandidate]:
        """Run a dense search query and return item IDs plus scores."""
        ...

    def search_sparse(
        self,
        *,
        vector: SparseVectorPayload,
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[SparseSearchCandidate]:
        """Run a sparse search query and return item IDs plus scores."""
        ...


class QdrantBackend:
    """Small REST client for the local Qdrant sidecar."""

    def __init__(self, config: QdrantConfig) -> None:
        self._config = config
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        """Return whether the backend is enabled in config."""
        return self._config.enabled

    def health(self) -> dict[str, Any]:
        """Return a lightweight health payload."""
        if not self._config.enabled:
            return {"enabled": False, "healthy": False, "reason": "disabled"}
        try:
            probe = self._probe_endpoint()
        except requests.RequestException as err:
            return {
                "enabled": True,
                "healthy": False,
                "collection": self._config.collection,
                "error": str(err),
            }
        return {
            "enabled": True,
            "healthy": True,
            "collection": self._config.collection,
            "probe": probe,
            "denseVectorName": self._config.dense_vector_name,
            "sparseVectorName": self._config.sparse_vector_name,
        }

    def collection_details(self) -> dict[str, Any]:
        """Return the live collection details when available."""
        if not self._config.enabled:
            return {}
        response = self._session.get(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}",
            headers=self._headers(),
            timeout=self._management_timeout_seconds(),
        )
        response.raise_for_status()
        body = _mapping_or_none(response.json())
        if body is None:
            return {}
        result = _mapping_value(body, "result")
        return {} if result is None else result

    def ensure_collection(self, *, vector_size: int, include_sparse: bool = False) -> None:
        """Create the collection when it does not exist."""
        if not self._config.enabled:
            return
        if vector_size <= 0:
            raise ValueError("vector_size must be positive")
        payload: dict[str, Any] = {
            "vectors": {
                self._config.dense_vector_name: {
                    "size": vector_size,
                    "distance": "Cosine",
                }
            }
        }
        if include_sparse:
            payload["sparse_vectors"] = {
                self._config.sparse_vector_name: {
                    "index": {
                        "on_disk": False,
                    }
                }
            }
        url = f"{self._config.url.rstrip('/')}/collections/{self._config.collection}"
        last_error: requests.RequestException | None = None
        for attempt in range(_COLLECTION_RETRY_ATTEMPTS):
            try:
                response = self._session.put(
                    url,
                    params={"timeout": str(self._management_commit_timeout_seconds())},
                    json=payload,
                    headers=self._headers(),
                    timeout=self._management_timeout_seconds() + 1.0,
                )
                if response.status_code != 409:
                    response.raise_for_status()
                self._wait_for_collection_ready(
                    vector_size=vector_size,
                    include_sparse=include_sparse,
                )
                return
            except requests.RequestException as err:
                last_error = err
                if attempt == _COLLECTION_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(0.25 * float(attempt + 1))
        if last_error is not None:
            raise last_error

    def upsert_points(self, points: Sequence[VectorPoint]) -> None:
        """Upsert dense and sparse points into the configured collection."""
        if not self._config.enabled or not points:
            return
        for start in range(0, len(points), _POINTS_WRITE_BATCH_SIZE):
            self._upsert_point_batch(points[start : start + _POINTS_WRITE_BATCH_SIZE])

    def _upsert_point_batch(self, points: Sequence[VectorPoint]) -> None:
        """Upsert one bounded point batch, splitting only when Qdrant times out."""
        url = f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points"
        last_error: requests.RequestException | None = None
        for attempt in range(_POINTS_WRITE_RETRY_ATTEMPTS):
            try:
                response = self._session.put(
                    url,
                    params={"wait": "true"},
                    json={"points": list(points)},
                    headers=self._headers(),
                    timeout=self._config.timeout_ms / 1000.0,
                )
                response.raise_for_status()
                return
            except requests.ReadTimeout as err:
                if len(points) == 1:
                    last_error = err
                    if attempt == _POINTS_WRITE_RETRY_ATTEMPTS - 1:
                        raise
                    time.sleep(0.25 * float(attempt + 1))
                    continue
                midpoint = len(points) // 2
                self._upsert_point_batch(points[:midpoint])
                self._upsert_point_batch(points[midpoint:])
                return
            except requests.RequestException as err:
                last_error = err
                if attempt == _POINTS_WRITE_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(0.25 * float(attempt + 1))
        if last_error is not None:
            raise last_error

    def delete_points(self, point_ids: Sequence[str]) -> None:
        """Delete stale point IDs from the configured collection."""
        if not self._config.enabled or not point_ids:
            return
        response = self._session.post(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points/delete",
            params={"wait": "true"},
            json={"points": list(point_ids)},
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()

    def search_dense(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[DenseSearchCandidate]:
        """Run a dense search query and return item IDs plus scores."""
        if not self._config.enabled:
            return []
        payload = self._query_payload(
            query=list(vector),
            using=self._config.dense_vector_name,
            limit=limit,
            mode=mode,
            scope=scope,
        )
        response = self._session.post(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points/query",
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        return self._parse_candidates(response.json(), candidate_type=DenseSearchCandidate)

    def search_sparse(
        self,
        *,
        vector: SparseVectorPayload,
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[SparseSearchCandidate]:
        """Run a sparse search query and return item IDs plus scores."""
        if not self._config.enabled:
            return []
        payload = self._query_payload(
            query=vector,
            using=self._config.sparse_vector_name,
            limit=limit,
            mode=mode,
            scope=scope,
        )
        response = self._session.post(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points/query",
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        return self._parse_candidates(response.json(), candidate_type=SparseSearchCandidate)

    def search(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[DenseSearchCandidate]:
        """Backward-compatible dense search alias."""
        return self.search_dense(vector=vector, limit=limit, mode=mode, scope=scope)

    def _management_timeout_seconds(self) -> float:
        """Return a startup-safe timeout for collection management calls."""
        return max(self._config.timeout_ms / 1000.0, 10.0)

    def _management_commit_timeout_seconds(self) -> int:
        """Return the server-side commit timeout for collection management calls."""
        return max(10, math.ceil(self._management_timeout_seconds()))

    def _probe_endpoint(self) -> str:
        """Return the best available probe endpoint for accepting traffic."""
        for endpoint in ("readyz", "healthz"):
            response = self._session.get(
                f"{self._config.url.rstrip('/')}/{endpoint}",
                headers=self._headers(),
                timeout=self._management_timeout_seconds(),
            )
            if endpoint == "readyz" and response.status_code == 404:
                continue
            response.raise_for_status()
            return endpoint
        raise RuntimeError("Qdrant did not expose a supported probe endpoint")

    def _wait_for_collection_ready(self, *, vector_size: int, include_sparse: bool) -> None:
        """Wait until the collection exposes the configured vector lanes."""
        last_error: Exception | None = None
        for attempt in range(_COLLECTION_READY_ATTEMPTS):
            try:
                details = self.collection_details()
            except requests.RequestException as err:
                last_error = err
            else:
                if self._collection_is_ready(
                    details=details,
                    vector_size=vector_size,
                    include_sparse=include_sparse,
                ):
                    return
                last_error = RuntimeError(
                    f"collection {self._config.collection} is missing expected vector lanes"
                )
            time.sleep(0.25 * float(attempt + 1))
        detail = "unknown error" if last_error is None else str(last_error)
        raise RuntimeError(
            f"Qdrant collection {self._config.collection} did not become ready: {detail}"
        )

    def _collection_is_ready(
        self,
        *,
        details: dict[str, Any],
        vector_size: int,
        include_sparse: bool,
    ) -> bool:
        """Return whether the live collection config matches the expected lanes."""
        config = _mapping_value(details, "config")
        if config is None:
            return False
        params = _mapping_value(config, "params")
        if params is None:
            return False
        vectors = _mapping_value(params, "vectors")
        if vectors is None:
            return False
        dense = _mapping_value(vectors, self._config.dense_vector_name)
        if dense is None:
            return False
        raw_size = dense.get("size")
        if not isinstance(raw_size, int) or raw_size != vector_size:
            return False
        if not include_sparse:
            return True
        sparse_vectors = _mapping_value(params, "sparse_vectors")
        if sparse_vectors is None:
            return False
        return _mapping_value(sparse_vectors, self._config.sparse_vector_name) is not None

    def _headers(self) -> dict[str, str]:
        """Return request headers."""
        headers = {"Content-Type": "application/json"}
        api_key = _resolve_api_key(
            api_key_env=self._config.api_key_env,
            api_key=self._config.api_key,
        )
        if api_key:
            headers["api-key"] = api_key
        return headers

    def _query_payload(
        self,
        *,
        query: list[float] | SparseVectorPayload,
        using: str,
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> dict[str, Any]:
        filter_payload = _build_filter(mode=mode, scope=scope)
        payload: dict[str, Any] = {
            "query": query,
            "using": using,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if filter_payload is not None:
            payload["filter"] = filter_payload
        return payload

    def _parse_candidates(
        self,
        payload: dict[str, Any],
        *,
        candidate_type: type[_CandidateT],
    ) -> list[_CandidateT]:
        results: object = payload.get("result")
        result_mapping = _mapping_or_none(results)
        if result_mapping is not None:
            results = result_mapping.get("points")
        if not isinstance(results, list):
            return []
        hits: list[_CandidateT] = []
        for raw_hit in cast(Sequence[object], results):
            hit = _mapping_or_none(raw_hit)
            if hit is None:
                continue
            payload_map = _mapping_value(hit, "payload")
            if payload_map is None:
                continue
            raw_item_id = payload_map.get("item_id")
            raw_point_id = hit.get("id")
            raw_score = hit.get("score")
            if not isinstance(raw_item_id, int):
                continue
            if not isinstance(raw_point_id, (str, int)):
                continue
            if not isinstance(raw_score, (int, float)):
                continue
            hits.append(
                candidate_type(
                    item_id=raw_item_id,
                    point_id=str(raw_point_id),
                    score=float(raw_score),
                )
            )
        return hits


def _build_filter(*, mode: SearchMode, scope: str | None) -> dict[str, Any] | None:
    """Build a Qdrant filter payload."""
    conditions: list[dict[str, Any]] = []
    if mode != "all":
        conditions.append({"key": "lane", "match": {"value": mode}})
    if scope:
        conditions.append(
            {
                "should": [
                    {"key": "scope", "match": {"value": scope}},
                    {"key": "scope", "match": {"value": "global"}},
                ]
            }
        )
    if not conditions:
        return None
    return {"must": conditions}


def _resolve_api_key(*, api_key_env: str | None, api_key: str | None) -> str | None:
    """Return the configured API key value, preferring the environment."""
    if api_key_env:
        resolved = os.environ.get(api_key_env)
        if resolved:
            return resolved
    if api_key:
        return api_key
    return None


def _mapping_or_none(value: object) -> dict[str, Any] | None:
    """Return a string-keyed mapping copy when *value* is mapping-like."""
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}


def _mapping_value(mapping: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """Return a nested mapping value by key when present."""
    return _mapping_or_none(mapping.get(key))
