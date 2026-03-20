"""Qdrant dense and sparse retrieval backend for strongclaw memory v2."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, TypeVar

import requests

from clawops.memory_v2.models import (
    DenseSearchCandidate,
    QdrantConfig,
    SearchMode,
    SparseSearchCandidate,
)

_CandidateT = TypeVar("_CandidateT", DenseSearchCandidate, SparseSearchCandidate)


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
            response = self._session.get(
                f"{self._config.url.rstrip('/')}/healthz",
                headers=self._headers(),
                timeout=self._config.timeout_ms / 1000.0,
            )
            response.raise_for_status()
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
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        body = response.json()
        result = body.get("result")
        return result if isinstance(result, dict) else {}

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
        response = self._session.put(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}",
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        if response.status_code == 409:
            return
        response.raise_for_status()

    def upsert_points(self, points: Sequence[dict[str, Any]]) -> None:
        """Upsert dense and sparse points into the configured collection."""
        if not self._config.enabled or not points:
            return
        response = self._session.put(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points",
            params={"wait": "true"},
            json={"points": list(points)},
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()

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
        vector: dict[str, list[int] | list[float]],
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
        query: list[float] | dict[str, list[int] | list[float]],
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
        results = payload.get("result")
        if isinstance(results, dict):
            results = results.get("points")
        if not isinstance(results, list):
            return []
        hits: list[_CandidateT] = []
        for raw_hit in results:
            if not isinstance(raw_hit, dict):
                continue
            payload_map = raw_hit.get("payload")
            if not isinstance(payload_map, dict):
                continue
            raw_item_id = payload_map.get("item_id")
            raw_point_id = raw_hit.get("id")
            raw_score = raw_hit.get("score")
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
