"""Shared data models for the strongclaw memory v2 engine."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

Lane = Literal["memory", "corpus"]
SearchMode = Literal["all", "memory", "corpus"]
SearchBackend = Literal["sqlite_fts", "qdrant_dense_hybrid"]
FusionMode = Literal["rrf", "weighted"]
EmbeddingProviderKind = Literal["disabled", "compatible-http"]
RerankProviderKind = Literal["none"]
EvidenceKind = Literal["file", "lcm_summary", "lcm_message_range", "external_uri"]

DEFAULT_MEMORY_FILE_NAMES = ("MEMORY.md", "memory.md")
DEFAULT_DAILY_DIR = "memory"
DEFAULT_BANK_DIR = "bank"
DEFAULT_DB_PATH = ".openclaw/memory-v2.sqlite"
DEFAULT_SNIPPET_CHARS = 400
DEFAULT_SEARCH_RESULTS = 8
DEFAULT_DEFAULT_SCOPE = "project:strongclaw"
DEFAULT_READABLE_SCOPE_PATTERNS = ("project:", "agent:", "user:", "global")
DEFAULT_WRITABLE_SCOPE_PATTERNS = ("project:", "agent:")
DEFAULT_AUTO_APPLY_SCOPE_PATTERNS = ("project:", "agent:")
DEFAULT_SEARCH_BACKEND: SearchBackend = "sqlite_fts"
DEFAULT_FALLBACK_BACKEND: SearchBackend = "sqlite_fts"
DEFAULT_EMBEDDING_PROVIDER: EmbeddingProviderKind = "disabled"
DEFAULT_RERANK_PROVIDER: RerankProviderKind = "none"
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_QDRANT_COLLECTION = "strongclaw-memory-v2"
EntryType = Literal[
    "fact",
    "reflection",
    "opinion",
    "entity",
    "proposal",
    "paragraph",
    "section",
]
ReflectionMode = Literal["safe", "propose", "apply"]


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusPathConfig:
    """Additional Markdown corpus path to index."""

    name: str
    path: pathlib.Path
    pattern: str


@dataclasses.dataclass(frozen=True, slots=True)
class GovernanceConfig:
    """Scope and write-governance configuration."""

    default_scope: str
    readable_scope_patterns: tuple[str, ...]
    writable_scope_patterns: tuple[str, ...]
    auto_apply_scope_patterns: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class RankingConfig:
    """Search ranking configuration."""

    memory_lane_weight: float = 1.0
    corpus_lane_weight: float = 1.0
    lexical_weight: float = 0.75
    coverage_weight: float = 0.35
    confidence_weight: float = 0.15
    recency_weight: float = 0.1
    contradiction_penalty: float = 0.2
    diversity_penalty: float = 0.35
    recency_half_life_days: int = 45


@dataclasses.dataclass(frozen=True, slots=True)
class BackendConfig:
    """Active and fallback search backend settings."""

    active: SearchBackend = DEFAULT_SEARCH_BACKEND
    fallback: SearchBackend = DEFAULT_FALLBACK_BACKEND


@dataclasses.dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Embedding provider configuration."""

    enabled: bool = False
    provider: EmbeddingProviderKind = DEFAULT_EMBEDDING_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key_env: str | None = None
    api_key: str | None = None
    dimensions: int | None = None
    batch_size: int = 32
    timeout_ms: int = 15_000


@dataclasses.dataclass(frozen=True, slots=True)
class RerankConfig:
    """Optional reranking configuration."""

    enabled: bool = False
    provider: RerankProviderKind = DEFAULT_RERANK_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key_env: str | None = None
    api_key: str | None = None
    timeout_ms: int = 15_000
    top_k: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class HybridConfig:
    """Hybrid retrieval configuration."""

    dense_candidate_pool: int = 24
    sparse_candidate_pool: int = 24
    vector_weight: float = 0.65
    text_weight: float = 0.35
    fusion: FusionMode = "rrf"
    rrf_k: int = 60
    rerank_top_k: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class QdrantConfig:
    """Dense vector backend configuration."""

    enabled: bool = False
    url: str = DEFAULT_QDRANT_URL
    collection: str = DEFAULT_QDRANT_COLLECTION
    timeout_ms: int = 3_000
    api_key_env: str | None = None
    api_key: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class InjectionConfig:
    """Prompt injection caps for auto-recall."""

    max_results: int = 3
    max_chars_per_result: int = 280


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryV2Config:
    """Validated memory-v2 configuration."""

    config_path: pathlib.Path
    workspace_root: pathlib.Path
    db_path: pathlib.Path
    memory_file_names: tuple[str, ...]
    daily_dir: str
    bank_dir: str
    include_default_memory: bool
    corpus_paths: tuple[CorpusPathConfig, ...]
    max_snippet_chars: int
    default_max_results: int
    governance: GovernanceConfig
    ranking: RankingConfig
    backend: BackendConfig = dataclasses.field(default_factory=BackendConfig)
    embedding: EmbeddingConfig = dataclasses.field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = dataclasses.field(default_factory=RerankConfig)
    hybrid: HybridConfig = dataclasses.field(default_factory=HybridConfig)
    qdrant: QdrantConfig = dataclasses.field(default_factory=QdrantConfig)
    injection: InjectionConfig = dataclasses.field(default_factory=InjectionConfig)

    @property
    def proposals_path(self) -> pathlib.Path:
        """Return the canonical proposal log path."""
        return self.workspace_root / self.bank_dir / "proposals.md"


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedItem:
    """Single indexed search item."""

    item_type: EntryType
    title: str
    snippet: str
    start_line: int
    end_line: int
    scope: str
    confidence: float | None = None
    entities: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    proposal_id: str | None = None
    proposal_status: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class IndexedDocument:
    """Materialized document ready to persist into the derived index."""

    rel_path: str
    abs_path: pathlib.Path
    lane: Lane
    source_name: str
    sha256: str
    line_count: int
    modified_at: str
    items: tuple[ParsedItem, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SearchExplanation:
    """Explainable ranking components for a search hit."""

    lexical_score: float
    lane_weight: float
    type_weight: float
    coverage_boost: float
    confidence_boost: float
    recency_boost: float
    contradiction_penalty: float
    dense_score: float = 0.0
    fusion_score: float = 0.0
    novelty_penalty: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert the explanation to a serializable mapping."""
        return {
            "lexicalScore": round(self.lexical_score, 6),
            "laneWeight": round(self.lane_weight, 6),
            "typeWeight": round(self.type_weight, 6),
            "coverageBoost": round(self.coverage_boost, 6),
            "confidenceBoost": round(self.confidence_boost, 6),
            "recencyBoost": round(self.recency_boost, 6),
            "contradictionPenalty": round(self.contradiction_penalty, 6),
            "denseScore": round(self.dense_score, 6),
            "fusionScore": round(self.fusion_score, 6),
            "noveltyPenalty": round(self.novelty_penalty, 6),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DenseSearchCandidate:
    """Dense search result from the vector backend."""

    item_id: int
    point_id: str
    score: float


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """Structured provenance reference for an indexed item."""

    kind: EvidenceKind
    relation: str = "supports"
    rel_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the entry to a serializable mapping."""
        payload: dict[str, Any] = {"kind": self.kind, "relation": self.relation}
        if self.rel_path is not None:
            payload["rel_path"] = self.rel_path
        if self.start_line is not None:
            payload["start_line"] = self.start_line
        if self.end_line is not None:
            payload["end_line"] = self.end_line
        if self.uri is not None:
            payload["uri"] = self.uri
        return payload

    def link_key(self) -> tuple[str, int, int, str] | None:
        """Return the SQLite evidence-link identity for file-backed entries."""
        if (
            self.kind != "file"
            or self.rel_path is None
            or self.start_line is None
            or self.end_line is None
        ):
            return None
        return (self.rel_path, self.start_line, self.end_line, self.relation)

    @classmethod
    def from_reference(
        cls,
        reference: str,
        *,
        relation: str = "supports",
    ) -> "EvidenceEntry":
        """Normalize one typed-entry evidence reference."""
        normalized = reference.strip()
        if not normalized:
            raise ValueError("evidence reference must not be blank")
        if normalized.startswith("lcm://"):
            return cls(kind=_lcm_kind(normalized), relation=relation, uri=normalized)
        if "://" in normalized and not normalized.startswith("file://"):
            return cls(kind="external_uri", relation=relation, uri=normalized)
        rel_path, start_line, end_line = _parse_file_reference(normalized)
        return cls(
            kind="file",
            relation=relation,
            rel_path=rel_path,
            start_line=start_line,
            end_line=end_line,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceEntry":
        """Load persisted evidence JSON while remaining backward-compatible."""
        kind = raw.get("kind")
        relation = str(raw.get("relation", "supports")).strip() or "supports"
        if not isinstance(kind, str):
            uri = raw.get("uri")
            if isinstance(uri, str) and uri.strip():
                return cls.from_reference(uri, relation=relation)
            rel_path = raw.get("rel_path")
            if isinstance(rel_path, str) and rel_path.strip():
                return cls(
                    kind="file",
                    relation=relation,
                    rel_path=rel_path.strip(),
                    start_line=_as_optional_int(raw.get("start_line")),
                    end_line=_as_optional_int(raw.get("end_line")),
                )
            raise ValueError("evidence entry requires either kind, uri, or rel_path")
        if kind == "file":
            rel_path = raw.get("rel_path")
            if not isinstance(rel_path, str) or not rel_path.strip():
                raise ValueError("file evidence requires rel_path")
            return cls(
                kind="file",
                relation=relation,
                rel_path=rel_path.strip(),
                start_line=_as_optional_int(raw.get("start_line")),
                end_line=_as_optional_int(raw.get("end_line")),
            )
        uri = raw.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            raise ValueError("external evidence requires uri")
        return cls(kind=_as_evidence_kind(kind), relation=relation, uri=uri.strip())


@dataclasses.dataclass(frozen=True, slots=True)
class SearchDiagnostics:
    """Latency and candidate counts captured during one search plan."""

    lexical_ms: float = 0.0
    sqlite_dense_ms: float = 0.0
    fusion_ms: float = 0.0
    lexical_candidates: int = 0
    dense_candidates: int = 0
    selected_candidates: int = 0

    def to_dict(self) -> dict[str, float | int]:
        """Convert diagnostics to telemetry-friendly scalars."""
        return {
            "lexicalMs": round(self.lexical_ms, 3),
            "sqliteDenseMs": round(self.sqlite_dense_ms, 3),
            "fusionMs": round(self.fusion_ms, 3),
            "lexicalCandidates": self.lexical_candidates,
            "denseCandidates": self.dense_candidates,
            "selectedCandidates": self.selected_candidates,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class SearchHit:
    """Search result payload compatible with OpenClaw memory tools."""

    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    source: Literal["memory"] = "memory"
    lane: Lane = "memory"
    item_type: str = "paragraph"
    confidence: float | None = None
    entities: tuple[str, ...] = ()
    scope: str | None = None
    evidence_count: int = 0
    contradiction_count: int = 0
    explanation: SearchExplanation | None = None
    backend: SearchBackend = DEFAULT_SEARCH_BACKEND

    def to_dict(self) -> dict[str, Any]:
        """Convert the hit to a serializable dictionary."""
        payload: dict[str, Any] = {
            "path": self.path,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "score": round(self.score, 6),
            "snippet": self.snippet,
            "source": self.source,
            "lane": self.lane,
            "itemType": self.item_type,
            "backend": self.backend,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.entities:
            payload["entities"] = list(self.entities)
        if self.scope:
            payload["scope"] = self.scope
        if self.evidence_count:
            payload["evidenceCount"] = self.evidence_count
        if self.contradiction_count:
            payload["contradictionCount"] = self.contradiction_count
        if self.explanation is not None:
            payload["explain"] = self.explanation.to_dict()
        return payload


@dataclasses.dataclass(frozen=True, slots=True)
class ReindexSummary:
    """Summary of a reindex run."""

    files: int
    chunks: int
    dirty: bool
    facts: int = 0
    opinions: int = 0
    reflections: int = 0
    entities: int = 0
    proposals: int = 0

    def to_dict(self) -> dict[str, int | bool]:
        """Convert the summary to a serializable dictionary."""
        return {
            "files": self.files,
            "chunks": self.chunks,
            "dirty": self.dirty,
            "facts": self.facts,
            "opinions": self.opinions,
            "reflections": self.reflections,
            "entities": self.entities,
            "proposals": self.proposals,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalRecord:
    """Canonical proposal derived from retained notes."""

    proposal_id: str
    kind: Literal["fact", "reflection", "opinion", "entity"]
    entry_line: str
    scope: str
    source_rel_path: str
    source_line: int
    status: Literal["pending", "applied"]
    entity: str | None = None
    confidence: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ReflectionSummary:
    """Reflection result payload."""

    proposed: int
    applied: int
    pending: int
    reflected: dict[str, int]
    index: ReindexSummary

    def to_dict(self) -> dict[str, Any]:
        """Convert the reflection summary to a serializable dictionary."""
        return {
            "ok": True,
            "proposed": self.proposed,
            "applied": self.applied,
            "pending": self.pending,
            "reflected": dict(self.reflected),
            "index": self.index.to_dict(),
        }


TYPE_ORDER: dict[EntryType, float] = {
    "fact": 1.15,
    "entity": 1.1,
    "opinion": 1.05,
    "reflection": 1.0,
    "proposal": 0.98,
    "paragraph": 0.9,
    "section": 0.8,
}


def normalize_text_tokens(text: str) -> tuple[str, ...]:
    """Return normalized lowercase tokens for similarity and coverage checks."""
    collapsed = "".join(character.lower() if character.isalnum() else " " for character in text)
    return tuple(token for token in collapsed.split() if token)


def evidence_labels(evidence: Sequence[str]) -> tuple[str, ...]:
    """Return deterministic evidence labels."""
    return tuple(sorted({entry.strip() for entry in evidence if entry.strip()}))


def _as_evidence_kind(value: str) -> EvidenceKind:
    """Validate a serialized evidence kind value."""
    if value not in {"file", "lcm_summary", "lcm_message_range", "external_uri"}:
        raise ValueError(f"unsupported evidence kind: {value}")
    return cast(EvidenceKind, value)


def _as_optional_int(value: object) -> int | None:
    """Coerce an optional integer field from persisted JSON."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("evidence line coordinates must be integers")
    return value


def _lcm_kind(uri: str) -> EvidenceKind:
    """Infer the structured LCM provenance kind from a URI."""
    if "/summary/" in uri:
        return _as_evidence_kind("lcm_summary")
    if "/messages/" in uri or "/message-range/" in uri:
        return _as_evidence_kind("lcm_message_range")
    return _as_evidence_kind("external_uri")


def _parse_file_reference(reference: str) -> tuple[str, int, int]:
    """Parse repo-relative file evidence references."""
    if "#" in reference:
        rel_path, line_part = reference.split("#", 1)
    else:
        rel_path, line_part = reference, ""
    rel_path = rel_path.strip()
    if not rel_path:
        raise ValueError("file evidence requires a relative path")
    start_line = 0
    end_line = 0
    if line_part.startswith("L"):
        line_text = line_part[1:]
        if "-L" in line_text:
            start_text, end_text = line_text.split("-L", 1)
            start_line = int(start_text)
            end_line = int(end_text)
        else:
            start_line = int(line_text)
            end_line = start_line
    return rel_path, start_line, end_line
