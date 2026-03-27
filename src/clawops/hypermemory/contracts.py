"""Typed service contracts and payloads for hypermemory."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

from clawops.hypermemory.models import IndexedDocument, ReindexSummary, SearchHit, SearchMode


class CorpusPathStatus(TypedDict):
    """One configured corpus path and its availability state."""

    name: str
    path: str
    pattern: str
    required: bool


class VectorPointPayload(TypedDict):
    """Qdrant payload stored alongside one vector point."""

    item_id: int
    rel_path: str
    lane: str
    source_name: str
    item_type: str
    scope: str
    start_line: int
    end_line: int
    modified_at: str
    confidence: float | None


class VectorRow(TypedDict):
    """Deterministic vector-sync row derived from one indexed item."""

    item_id: int
    point_id: str
    content: str
    payload: VectorPointPayload
    sparse_term_count: NotRequired[int]


type SparseVectorPayload = dict[str, list[int] | list[float]]


class VectorPoint(TypedDict):
    """Dense and sparse vectors plus Qdrant payload for one point."""

    id: str
    vector: dict[str, list[float] | SparseVectorPayload]
    payload: VectorPointPayload


@dataclass(frozen=True, slots=True)
class EmbeddedVectorRow:
    """One embedded row ready for vector-backend synchronization."""

    row: VectorRow
    dense_vector: list[float]
    sparse_vector: SparseVectorPayload | None


class ReadResult(TypedDict):
    """Public payload returned by ``HypermemoryEngine.read``."""

    path: str
    text: str


class StatusResult(TypedDict):
    """Public payload returned by ``HypermemoryEngine.status``."""

    ok: bool
    provider: str
    schemaVersion: int
    workspaceRoot: str
    dbPath: str
    dirty: bool
    backendActive: str
    backendFallback: str
    backendConfigDirty: bool
    documents: int
    searchItems: int
    vectorItems: int
    sparseVectorItems: int
    sparseVocabularySize: int
    facts: int
    opinions: int
    reflections: int
    entities: int
    proposals: int
    conflicts: int
    factRegistryEntries: int
    embeddingEnabled: bool
    embeddingProvider: str
    embeddingModel: str
    rerankEnabled: bool
    rerankProvider: str
    rerankFallbackProvider: str
    rerankFailOpen: bool
    rerankModel: str
    rerankDevice: str
    rerankResolvedDevice: str
    rerankFallbackModel: str
    rerankCandidatePool: int
    rerankOperationalRequired: bool
    qdrantEnabled: bool
    qdrantHealthy: bool
    qdrant: dict[str, object]
    lastVectorSyncAt: str | None
    lastVectorSyncError: str | None
    sparseFingerprint: str | None
    sparseFingerprintDirty: bool
    sparseDocumentCount: int
    sparseAverageDocumentLength: float
    defaultScope: str
    readableScopes: list[str]
    writableScopes: list[str]
    autoApplyScopes: list[str]
    missingCorpusPaths: list[CorpusPathStatus]


class VerifyLaneResult(TypedDict):
    """Latency and hit count for one verification lane."""

    hits: int
    ms: float


class VerifyRerankResult(TypedDict):
    """Rerank verification details."""

    required: bool
    candidatePool: int
    documents: NotRequired[int]
    provider: NotRequired[str]
    fallbackUsed: NotRequired[bool]
    candidateCount: NotRequired[int]
    rerankMs: NotRequired[float]


class VerifyLaneChecks(TypedDict):
    """Detailed lane checks returned by hypermemory verification."""

    probeQuery: str
    dense: NotRequired[VerifyLaneResult]
    sparse: NotRequired[VerifyLaneResult]
    rerank: VerifyRerankResult


class VerifyResult(TypedDict):
    """Public payload returned by ``HypermemoryEngine.verify``."""

    ok: bool
    provider: str
    backend: str
    status: StatusResult
    collection: dict[str, object]
    laneChecks: VerifyLaneChecks
    errors: list[str]


class BenchmarkCase(TypedDict):
    """One benchmark case for the engine benchmark runner."""

    name: str
    query: str
    expectedPaths: list[str]
    maxResults: NotRequired[int]
    lane: NotRequired[SearchMode]


class BenchmarkCaseResult(TypedDict):
    """One benchmark result row."""

    name: str
    query: str
    expectedPaths: list[str]
    actualPaths: list[str]
    passed: bool


class BenchmarkResult(TypedDict):
    """Public payload returned by ``HypermemoryEngine.benchmark_cases``."""

    provider: str
    cases: list[BenchmarkCaseResult]
    passed: int
    total: int


class MemoryProMetadata(TypedDict):
    """Hypermemory-specific metadata exported to Memory Pro."""

    itemType: str
    scope: str
    sourcePath: str
    startLine: int
    endLine: int
    entities: list[str]
    evidence: list[dict[str, object]]
    confidence: NotRequired[float]


class MemoryProRecordMetadata(TypedDict):
    """Per-memory metadata envelope."""

    source: str
    hypermemory: MemoryProMetadata


class MemoryProRecord(TypedDict):
    """One Memory Pro import record."""

    id: str
    text: str
    category: str
    importance: float
    timestamp: int
    metadata: MemoryProRecordMetadata


class MemoryProImportResult(TypedDict):
    """Public payload returned by ``HypermemoryEngine.export_memory_pro_import``."""

    provider: str
    scope: str
    includeDaily: bool
    memories: list[MemoryProRecord]


class FlushMetadataResult(TypedDict):
    """Result payload for canonical metadata flushes."""

    ok: bool
    updatedFiles: int
    updatedEntries: int


class QueryDeps(Protocol):
    """Dependency surface required by ``QueryService`` beyond its core services."""

    def iter_documents(self) -> tuple[IndexedDocument, ...]: ...

    def missing_corpus_paths(self) -> list[CorpusPathStatus]: ...

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary: ...

    def get_fact(
        self,
        fact_key: str,
        *,
        conn: sqlite3.Connection | None = None,
        scope: str | None = None,
    ) -> SearchHit | None: ...


class IndexingDeps(Protocol):
    """Dependency surface required by ``IndexingService``."""

    def flush_metadata(self) -> FlushMetadataResult: ...


class VerificationDeps(Protocol):
    """Dependency surface required by ``VerificationService``."""

    def status(self) -> StatusResult: ...

    def missing_required_corpus_paths(self) -> list[CorpusPathStatus]: ...
