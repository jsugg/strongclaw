"""Unit tests for the hybrid codebase context lane."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from clawops.common import write_yaml
from clawops.context.codebase.service import CodebaseContextService, service_from_config
from clawops.hypermemory.contracts import SparseVectorPayload, VectorPoint
from clawops.hypermemory.models import DenseSearchCandidate, RerankResponse, SparseSearchCandidate


class _FakeEmbeddingProvider:
    """Deterministic embedding provider for hybrid tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.casefold()
            if "auth.py" in lowered or "token_guard" in lowered or "credential rotation" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return vectors


class _FakeVectorBackend:
    """Fake Qdrant-like backend for hybrid tests."""

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.ensured: list[tuple[int, bool]] = []
        self.upserted: list[list[VectorPoint]] = []
        self.deleted: list[list[str]] = []
        self.dense_candidates: list[DenseSearchCandidate] = []
        self.sparse_candidates: list[SparseSearchCandidate] = []

    def health(self) -> dict[str, object]:
        if not self._healthy:
            return {"enabled": True, "healthy": False, "reason": "offline"}
        return {"enabled": True, "healthy": True}

    def collection_details(self) -> dict[str, object]:
        return {}

    def ensure_collection(self, *, vector_size: int, include_sparse: bool = False) -> None:
        self.ensured.append((vector_size, include_sparse))

    def upsert_points(self, points: Sequence[VectorPoint]) -> None:
        self.upserted.append(list(points))

    def delete_points(self, point_ids: Sequence[str]) -> None:
        self.deleted.append(list(point_ids))

    def search_dense(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        mode: str,
        scope: str | None,
    ) -> list[DenseSearchCandidate]:
        del vector, limit, mode, scope
        return list(self.dense_candidates)

    def search_sparse(
        self,
        *,
        vector: SparseVectorPayload,
        limit: int,
        mode: str,
        scope: str | None,
    ) -> list[SparseSearchCandidate]:
        del vector, limit, mode, scope
        return list(self.sparse_candidates)


class _FakeRerankProvider:
    """Rerank provider that returns precomputed scores."""

    def __init__(self, scores: tuple[float, ...]) -> None:
        self._scores = scores
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        self.calls.append((query, tuple(documents)))
        return RerankResponse(
            scores=self._scores[: len(documents)],
            provider="compatible-http",
            applied=True,
        )


def _write_hybrid_config(path: pathlib.Path) -> None:
    write_yaml(
        path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {"enabled": False},
            "paths": {"include": ["**/*.py"]},
            "embedding": {
                "enabled": True,
                "provider": "compatible-http",
                "model": "dummy-embedding",
                "base_url": "http://127.0.0.1:9999/v1",
                "batch_size": 4,
                "timeout_ms": 500,
            },
            "rerank": {
                "enabled": True,
                "provider": "compatible-http",
                "fallback_provider": "none",
                "fail_open": True,
                "normalize_scores": True,
                "compatible_http": {
                    "model": "dummy-rerank",
                    "base_url": "http://127.0.0.1:9998/v1",
                    "timeout_ms": 500,
                },
            },
            "hybrid": {
                "dense_candidate_pool": 6,
                "sparse_candidate_pool": 6,
                "vector_weight": 0.65,
                "text_weight": 0.35,
                "fusion": "rrf",
                "rrf_k": 10,
                "rerank_candidate_pool": 4,
            },
            "qdrant": {
                "enabled": True,
                "url": "http://127.0.0.1:6333",
                "collection": "test-codebase-context",
                "dense_vector_name": "dense",
                "sparse_vector_name": "sparse",
                "timeout_ms": 500,
            },
        },
    )


def _build_service(tmp_path: pathlib.Path) -> tuple[pathlib.Path, CodebaseContextService]:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = tmp_path / "context.yaml"
    _write_hybrid_config(config_path)
    service = service_from_config(config_path, repo, scale="medium")
    return repo, service


def test_medium_scale_worker_syncs_chunk_vectors_when_hybrid_enabled(
    tmp_path: pathlib.Path,
) -> None:
    repo, service = _build_service(tmp_path)
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'auth'\n",
        encoding="utf-8",
    )
    (repo / "notes.py").write_text(
        "def review_notes():\n    return 'notes'\n",
        encoding="utf-8",
    )

    fake_embedder = _FakeEmbeddingProvider()
    fake_backend = _FakeVectorBackend()
    service.override_runtime_deps(
        embedding_provider=fake_embedder,
        vector_backend=fake_backend,
    )

    count = service.index()

    assert count == 2
    assert fake_backend.ensured == []
    assert fake_backend.upserted == []
    assert fake_embedder.calls == []
    with service.connect() as conn:
        vector_rows = int(conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0])
        sparse_terms = int(conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0])
    assert vector_rows == 0
    assert sparse_terms == 0

    service.consolidate_runtime_artifacts()

    assert fake_backend.ensured == [(3, True)]
    assert len(fake_backend.upserted) == 1
    assert fake_embedder.calls
    with service.connect() as conn:
        vector_rows = int(conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0])
        sparse_terms = int(conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0])
    assert vector_rows >= 2
    assert sparse_terms > 0


def test_medium_scale_query_uses_hybrid_fusion_and_rerank(tmp_path: pathlib.Path) -> None:
    repo, service = _build_service(tmp_path)
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'auth token rotation'\n",
        encoding="utf-8",
    )
    (repo / "guide.py").write_text(
        "def guide():\n    return 'credential rotation credential rotation'\n",
        encoding="utf-8",
    )

    fake_embedder = _FakeEmbeddingProvider()
    fake_backend = _FakeVectorBackend()
    fake_reranker = _FakeRerankProvider((0.95, 0.05))
    service.override_runtime_deps(
        embedding_provider=fake_embedder,
        rerank_provider=fake_reranker,
        vector_backend=fake_backend,
    )

    service.index()
    service.consolidate_runtime_artifacts()
    with service.connect() as conn:
        item_rows = conn.execute(
            "SELECT path, item_id FROM chunk_vectors ORDER BY path ASC, item_id ASC"
        ).fetchall()
    item_ids = {str(row["path"]): int(row["item_id"]) for row in item_rows}
    fake_backend.dense_candidates = [
        DenseSearchCandidate(item_id=item_ids["auth.py"], point_id="auth", score=0.9)
    ]
    fake_backend.sparse_candidates = [
        SparseSearchCandidate(item_id=item_ids["auth.py"], point_id="auth", score=0.8)
    ]

    hits = service.query("credential rotation", limit=2)
    pack = service.pack("credential rotation", limit=2)

    assert hits
    assert hits[0].path == "auth.py"
    assert fake_reranker.calls
    assert "- backend_modes: lexical, hybrid" in pack


def test_medium_scale_falls_back_to_lexical_when_hybrid_backend_is_unhealthy(
    tmp_path: pathlib.Path,
) -> None:
    repo, service = _build_service(tmp_path)
    (repo / "guide.py").write_text(
        "def guide():\n    return 'credential rotation'\n",
        encoding="utf-8",
    )

    service.override_runtime_deps(
        embedding_provider=_FakeEmbeddingProvider(),
        vector_backend=_FakeVectorBackend(healthy=False),
    )

    service.index()
    service.consolidate_runtime_artifacts()
    hits = service.query("credential rotation", limit=1)
    pack = service.pack("credential rotation", limit=1)

    assert hits
    assert hits[0].path == "guide.py"
    assert "- backend_modes: lexical" in pack


def test_medium_scale_deletion_keeps_hybrid_pending_until_worker_sync(
    tmp_path: pathlib.Path,
) -> None:
    repo, service = _build_service(tmp_path)
    obsolete = repo / "obsolete.py"
    obsolete.write_text(
        "def stale_path():\n    return 'obsolete'\n",
        encoding="utf-8",
    )
    (repo / "active.py").write_text(
        "def active_path():\n    return 'active'\n",
        encoding="utf-8",
    )

    fake_backend = _FakeVectorBackend()
    service.override_runtime_deps(
        embedding_provider=_FakeEmbeddingProvider(),
        vector_backend=fake_backend,
    )

    service.index()
    service.consolidate_runtime_artifacts()
    with service.connect() as conn:
        stale_point_ids = [
            str(row["point_id"])
            for row in conn.execute(
                "SELECT point_id FROM chunk_vectors WHERE path = ? ORDER BY point_id ASC",
                ("obsolete.py",),
            ).fetchall()
        ]

    obsolete.unlink()
    service.index()

    assert service.backend_modes() == ("lexical",)
    with service.connect() as conn:
        pending = [
            str(row["point_id"])
            for row in conn.execute(
                "SELECT point_id FROM hybrid_pending_deletions ORDER BY point_id ASC"
            ).fetchall()
        ]
    assert pending == stale_point_ids

    service.consolidate_runtime_artifacts()

    assert service.backend_modes() == ("lexical", "hybrid")
    assert fake_backend.deleted
    assert set(fake_backend.deleted[-1]) == set(stale_point_ids)
