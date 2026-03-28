"""Shared StrongClaw hypermemory test helpers."""

from __future__ import annotations

import pathlib
import textwrap
from collections.abc import Callable, Sequence
from typing import TypedDict

from clawops.hypermemory import DenseSearchCandidate, RerankResponse, SparseSearchCandidate
from clawops.hypermemory.contracts import SparseVectorPayload, VectorPoint
from clawops.hypermemory.models import RerankProviderKind, SearchMode

type HypermemoryWorkspaceFactory = Callable[[], pathlib.Path]
type HypermemoryConfigWriter = Callable[[pathlib.Path, pathlib.Path], None]


class RerankCall(TypedDict):
    query: str
    documents: list[str]


def write_hypermemory_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    """Write a minimal operator-shaped hypermemory config for tests."""
    del workspace_root
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
                - memory.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )


def build_workspace(tmp_path: pathlib.Path, *, include_daily_memory: bool = True) -> pathlib.Path:
    """Create a baseline workspace with docs, memory, and bank surfaces."""
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "memory").mkdir(parents=True)
    (workspace / "bank").mkdir(parents=True)
    (workspace / "MEMORY.md").write_text(
        "# Project Memory\n\n- Fact: The deploy process uses blue/green cutovers.\n",
        encoding="utf-8",
    )
    if include_daily_memory:
        (workspace / "memory" / "2026-03-16.md").write_text(
            """
            # Daily Log

            ## Retain
            - Fact: Alice owns the deployment playbook.
            - Opinion[c=0.90]: QMD improves recall but should surface degraded mode.
            - Entity[Alice]: Maintains the gateway rollout checklist.
            """.strip() + "\n",
            encoding="utf-8",
        )
    (workspace / "docs" / "runbook.md").write_text(
        """
        # Gateway Runbook

        Rotate the gateway token before enabling a new browser profile.
        """.strip() + "\n",
        encoding="utf-8",
    )
    return workspace


def build_rerank_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a workspace tuned to test rerank reordering behavior."""
    workspace = build_workspace(tmp_path)
    (workspace / "MEMORY.md").write_text(
        """
        # Project Memory

        - Fact: Gateway token deploy checklist lives beside the release notes.
        """.strip() + "\n",
        encoding="utf-8",
    )
    (workspace / "docs" / "runbook.md").write_text(
        """
        # Gateway Runbook

        The browser profile rollout follows the gateway token deploy checklist.
        """.strip() + "\n",
        encoding="utf-8",
    )
    return workspace


class FakeEmbeddingProvider:
    """Return a static dense embedding for all inputs."""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[list[str]] = []

    def embed_texts(
        self, texts: Sequence[str], *, timeout_ms: int | None = None
    ) -> list[list[float]]:
        del timeout_ms
        self.calls.append(list(texts))
        return [list(self.vector) for _ in texts]


class FakeQdrantBackend:
    """In-memory Qdrant double used for engine unit tests."""

    def __init__(self) -> None:
        self.ensure_calls: list[int] = []
        self.upsert_calls: list[list[VectorPoint]] = []
        self.delete_calls: list[list[str]] = []
        self.dense_limits: list[int] = []
        self.sparse_limits: list[int] = []
        self.search_results: list[DenseSearchCandidate] = []
        self.sparse_search_results: list[SparseSearchCandidate] = []
        self.raise_on_search = False
        self.raise_on_ensure_collection = False
        self.raise_on_upsert = False
        self.include_sparse_calls: list[bool] = []
        self.health_payload: dict[str, object] = {
            "enabled": True,
            "healthy": True,
            "collection": "test",
        }
        self.collection_details_payload: dict[str, object] = {
            "config": {
                "params": {
                    "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
                    "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
                }
            }
        }

    def health(self) -> dict[str, object]:
        return dict(self.health_payload)

    def collection_details(self) -> dict[str, object]:
        return dict(self.collection_details_payload)

    def ensure_collection(self, *, vector_size: int, include_sparse: bool = False) -> None:
        self.ensure_calls.append(vector_size)
        self.include_sparse_calls.append(include_sparse)
        if self.raise_on_ensure_collection:
            raise RuntimeError("qdrant collection warmup timed out")

    def upsert_points(self, points: Sequence[VectorPoint]) -> None:
        self.upsert_calls.append(list(points))
        if self.raise_on_upsert:
            raise RuntimeError("qdrant upsert failed")

    def delete_points(self, point_ids: Sequence[str]) -> None:
        self.delete_calls.append(list(point_ids))

    def search_dense(
        self, *, vector: Sequence[float], limit: int, mode: SearchMode, scope: str | None
    ) -> list[DenseSearchCandidate]:
        del vector, mode, scope
        self.dense_limits.append(limit)
        if self.raise_on_search:
            raise RuntimeError("dense backend unavailable")
        return list(self.search_results)

    def search_sparse(
        self,
        *,
        vector: SparseVectorPayload,
        limit: int,
        mode: SearchMode,
        scope: str | None,
    ) -> list[SparseSearchCandidate]:
        del vector, mode, scope
        self.sparse_limits.append(limit)
        return list(self.sparse_search_results)

    def search(
        self, *, vector: Sequence[float], limit: int, mode: SearchMode, scope: str | None
    ) -> list[DenseSearchCandidate]:
        return self.search_dense(vector=vector, limit=limit, mode=mode, scope=scope)


class StaticRerankProvider:
    """Return predetermined rerank scores for each candidate set."""

    def __init__(
        self,
        scores: Sequence[float],
        *,
        provider: RerankProviderKind = "local-sentence-transformers",
        fallback_used: bool = False,
    ) -> None:
        self._scores: tuple[float, ...] = tuple(scores)
        self._provider: RerankProviderKind = provider
        self._fallback_used: bool = fallback_used
        self.calls: list[RerankCall] = []

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        self.calls.append({"query": query, "documents": list(documents)})
        return RerankResponse(
            scores=self._scores,
            provider=self._provider,
            applied=True,
            fallback_used=self._fallback_used,
        )


class FailingRerankProvider:
    """Raise a deterministic failure for fail-open tests."""

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        del query, documents
        raise RuntimeError("rerank backend unavailable")
