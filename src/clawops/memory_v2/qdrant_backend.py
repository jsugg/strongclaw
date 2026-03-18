"""Qdrant dense retrieval backend for strongclaw memory v2."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import requests

from clawops.memory_v2.models import DenseSearchCandidate, QdrantConfig, SearchMode


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
        return {"enabled": True, "healthy": True, "collection": self._config.collection}

    def ensure_collection(self, *, vector_size: int) -> None:
        """Create the collection when it does not exist."""
        if not self._config.enabled:
            return
        if vector_size <= 0:
            raise ValueError("vector_size must be positive")
        response = self._session.put(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}",
            json={"vectors": {"size": vector_size, "distance": "Cosine"}},
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()

    def upsert_points(self, points: Sequence[dict[str, Any]]) -> None:
        """Upsert dense points into the configured collection."""
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

    def search(
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
        filter_payload = _build_filter(mode=mode, scope=scope)
        payload: dict[str, Any] = {
            "query": list(vector),
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if filter_payload:
            payload["filter"] = filter_payload
        response = self._session.post(
            f"{self._config.url.rstrip('/')}/collections/{self._config.collection}/points/query",
            json=payload,
            headers=self._headers(),
            timeout=self._config.timeout_ms / 1000.0,
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("result")
        if isinstance(results, dict):
            results = results.get("points")
        if not isinstance(results, list):
            return []
        hits: list[DenseSearchCandidate] = []
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
                DenseSearchCandidate(
                    item_id=raw_item_id,
                    point_id=str(raw_point_id),
                    score=float(raw_score),
                )
            )
        return hits

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
