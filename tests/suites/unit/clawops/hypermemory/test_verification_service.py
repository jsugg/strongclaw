"""Unit tests for hypermemory/services/verification_service.py."""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import replace
from typing import cast

import pytest

from clawops.hypermemory import load_config
from clawops.hypermemory.contracts import CorpusPathStatus, StatusResult, VerificationDeps
from clawops.hypermemory.models import HypermemoryConfig
from clawops.hypermemory.schema import SCHEMA_VERSION, ensure_schema
from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.services.verification_service import VerificationService
from tests.utils.helpers.hypermemory import (
    FailingRerankProvider,
    FakeEmbeddingProvider,
    FakeQdrantBackend,
    StaticRerankProvider,
    build_workspace,
    write_hypermemory_config,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_config(tmp_path: pathlib.Path) -> HypermemoryConfig:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    return load_config(config_path)  # type: ignore[return-value]


def _make_status_result(
    *,
    dirty: bool = False,
    qdrant_enabled: bool = False,
    qdrant_healthy: bool = False,
    vector_items: int = 0,
    sparse_vector_items: int = 0,
    sparse_fingerprint_dirty: bool = False,
    last_vector_sync_error: str | None = None,
    vector_sync_deferred: bool = False,
    search_items: int = 0,
) -> StatusResult:
    return cast(
        StatusResult,
        {
            "ok": True,
            "provider": "strongclaw-hypermemory",
            "schemaVersion": SCHEMA_VERSION,
            "workspaceRoot": "/ws",
            "dbPath": "/ws/db.sqlite",
            "dirty": dirty,
            "backendActive": "sqlite_fts",
            "backendFallback": "sqlite_fts",
            "backendConfigDirty": False,
            "documents": 0,
            "searchItems": search_items,
            "vectorItems": vector_items,
            "sparseVectorItems": sparse_vector_items,
            "sparseVocabularySize": 0,
            "facts": 0,
            "opinions": 0,
            "reflections": 0,
            "entities": 0,
            "proposals": 0,
            "conflicts": 0,
            "factRegistryEntries": 0,
            "embeddingEnabled": False,
            "embeddingProvider": "local",
            "embeddingModel": "all-MiniLM-L6-v2",
            "rerankEnabled": False,
            "rerankProvider": "none",
            "rerankFallbackProvider": "none",
            "rerankFailOpen": True,
            "rerankModel": "none",
            "rerankDevice": "cpu",
            "rerankResolvedDevice": "cpu",
            "rerankFallbackModel": "none",
            "rerankCandidatePool": 0,
            "rerankOperationalRequired": False,
            "qdrantEnabled": qdrant_enabled,
            "qdrantHealthy": qdrant_healthy,
            "qdrant": {"enabled": qdrant_enabled, "healthy": qdrant_healthy},
            "lastVectorSyncAt": None,
            "lastVectorSyncError": last_vector_sync_error,
            "vectorSyncDeferred": vector_sync_deferred,
            "sparseFingerprint": None,
            "sparseFingerprintDirty": sparse_fingerprint_dirty,
            "sparseDocumentCount": 0,
            "sparseAverageDocumentLength": 0.0,
            "defaultScope": "global",
            "readableScopes": ["global"],
            "writableScopes": ["global"],
            "autoApplyScopes": [],
            "missingCorpusPaths": [],
        },
    )


class _FakeVerificationDeps:
    def __init__(
        self,
        *,
        status_result: StatusResult,
        missing_paths: list[CorpusPathStatus] | None = None,
    ) -> None:
        self._status = status_result
        self._missing_paths: list[CorpusPathStatus] = missing_paths or []

    def status(self) -> StatusResult:
        return self._status

    def missing_required_corpus_paths(self) -> list[CorpusPathStatus]:
        return self._missing_paths


def _make_verification_service(
    config: HypermemoryConfig,
    conn: sqlite3.Connection,
    *,
    status_result: StatusResult | None = None,
    missing_paths: list[CorpusPathStatus] | None = None,
    rerank_provider: StaticRerankProvider | FailingRerankProvider | None = None,
    vector_backend: FakeQdrantBackend | None = None,
) -> VerificationService:
    fake_vec = vector_backend or FakeQdrantBackend()
    fake_emb = FakeEmbeddingProvider([0.1, 0.2, 0.3])
    index = IndexService(connect=lambda: conn)
    backend = BackendService(
        config=config,
        embedding_provider=fake_emb,
        vector_backend=fake_vec,
        index=index,
    )
    rp = rerank_provider or StaticRerankProvider([0.9, 0.8])
    deps: VerificationDeps = _FakeVerificationDeps(
        status_result=status_result or _make_status_result(),
        missing_paths=missing_paths,
    )
    return VerificationService(
        config=config,
        connect=lambda: conn,
        backend=backend,
        vector_backend=fake_vec,
        rerank_provider=rp,
        deps=deps,
    )


def test_observed_rerank_scorer_returns_response(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc = _make_verification_service(
        config,
        conn,
        rerank_provider=StaticRerankProvider([0.9, 0.7]),
    )
    response = svc.observed_rerank_scorer("query", ["doc1", "doc2"])
    assert response.applied
    assert len(response.scores) == 2


def test_observed_rerank_scorer_empty_documents_returns_empty(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc = _make_verification_service(config, conn)
    response = svc.observed_rerank_scorer("query", [])
    assert not response.applied
    assert response.scores == ()


def test_observed_rerank_scorer_fail_open_returns_error_response(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        rerank=replace(config.rerank, fail_open=True),
    )
    conn = _make_conn()
    svc = _make_verification_service(
        cfg,
        conn,
        rerank_provider=FailingRerankProvider(),
    )
    response = svc.observed_rerank_scorer("query", ["doc1"])
    assert response.fail_open
    assert response.error is not None


def test_observed_rerank_scorer_fail_closed_raises(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        rerank=replace(config.rerank, fail_open=False),
    )
    conn = _make_conn()
    svc = _make_verification_service(
        cfg,
        conn,
        rerank_provider=FailingRerankProvider(),
    )
    with pytest.raises(RuntimeError):
        svc.observed_rerank_scorer("query", ["doc1"])


def test_rerank_resolved_device_no_method_returns_empty(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    # StaticRerankProvider has no resolved_device method
    svc = _make_verification_service(
        config,
        conn,
        rerank_provider=StaticRerankProvider([0.9]),
    )
    assert svc.rerank_resolved_device() == ""


class _RerankProviderWithDevice:
    def score(self, query: str, documents: object) -> object:
        del query, documents
        from clawops.hypermemory.models import RerankResponse

        return RerankResponse(scores=(0.9,), provider="local-sentence-transformers", applied=True)

    def resolved_device(self) -> str:
        return "mps"


def test_rerank_resolved_device_callable_returns_value(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc = _make_verification_service(
        config,
        conn,
        rerank_provider=_RerankProviderWithDevice(),  # type: ignore[arg-type]
    )
    assert svc.rerank_resolved_device() == "mps"


def test_verify_fails_when_backend_not_sparse_dense_hybrid(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # Default config uses sqlite_fts, not qdrant_sparse_dense_hybrid
    conn = _make_conn()
    svc = _make_verification_service(config, conn)
    result = svc.verify()
    assert not result["ok"]
    assert any("backend.active" in e for e in result["errors"])


def test_verify_fails_when_missing_required_corpus_paths(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid"),
    )
    missing: list[CorpusPathStatus] = [
        {"name": "docs", "path": "docs", "pattern": "**/*.md", "required": True}
    ]
    conn = _make_conn()
    svc = _make_verification_service(
        cfg,
        conn,
        missing_paths=missing,
    )
    result = svc.verify()
    assert not result["ok"]
    assert any("required corpus paths" in e for e in result["errors"])


def test_verify_fails_when_qdrant_not_healthy(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid"),
    )
    status = _make_status_result(qdrant_enabled=False, qdrant_healthy=False)
    conn = _make_conn()
    svc = _make_verification_service(cfg, conn, status_result=status)
    result = svc.verify()
    assert not result["ok"]
    assert any("Qdrant" in e for e in result["errors"])
