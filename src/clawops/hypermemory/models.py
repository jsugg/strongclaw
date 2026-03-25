"""Shared data models for the StrongClaw hypermemory engine."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from clawops.hypermemory.defaults import (
    ADMISSION_DEFAULTS,
    ADMISSION_TYPE_PRIORS_DEFAULTS,
    BACKEND_DEFAULTS,
    CAPTURE_DEFAULTS,
    CAPTURE_LLM_DEFAULTS,
    DECAY_DEFAULTS,
    DEDUP_DEFAULTS,
)
from clawops.hypermemory.defaults import (
    DEFAULT_AUTO_APPLY_SCOPE_PATTERNS as RESOURCE_DEFAULT_AUTO_APPLY_SCOPE_PATTERNS,
)
from clawops.hypermemory.defaults import DEFAULT_BANK_DIR as RESOURCE_DEFAULT_BANK_DIR
from clawops.hypermemory.defaults import DEFAULT_DAILY_DIR as RESOURCE_DEFAULT_DAILY_DIR
from clawops.hypermemory.defaults import DEFAULT_DB_PATH as RESOURCE_DEFAULT_DB_PATH
from clawops.hypermemory.defaults import DEFAULT_DEFAULT_SCOPE as RESOURCE_DEFAULT_DEFAULT_SCOPE
from clawops.hypermemory.defaults import (
    DEFAULT_EMBEDDING_PROVIDER_LITERAL,
    DEFAULT_FALLBACK_BACKEND_LITERAL,
)
from clawops.hypermemory.defaults import (
    DEFAULT_MEMORY_FILE_NAMES as RESOURCE_DEFAULT_MEMORY_FILE_NAMES,
)
from clawops.hypermemory.defaults import (
    DEFAULT_QDRANT_COLLECTION as RESOURCE_DEFAULT_QDRANT_COLLECTION,
)
from clawops.hypermemory.defaults import (
    DEFAULT_QDRANT_DENSE_VECTOR_NAME as RESOURCE_DEFAULT_QDRANT_DENSE_VECTOR_NAME,
)
from clawops.hypermemory.defaults import (
    DEFAULT_QDRANT_SPARSE_VECTOR_NAME as RESOURCE_DEFAULT_QDRANT_SPARSE_VECTOR_NAME,
)
from clawops.hypermemory.defaults import DEFAULT_QDRANT_URL as RESOURCE_DEFAULT_QDRANT_URL
from clawops.hypermemory.defaults import (
    DEFAULT_READABLE_SCOPE_PATTERNS as RESOURCE_DEFAULT_READABLE_SCOPE_PATTERNS,
)
from clawops.hypermemory.defaults import (
    DEFAULT_RERANK_PROVIDER_LITERAL,
    DEFAULT_SEARCH_BACKEND_LITERAL,
)
from clawops.hypermemory.defaults import DEFAULT_SEARCH_RESULTS as RESOURCE_DEFAULT_SEARCH_RESULTS
from clawops.hypermemory.defaults import DEFAULT_SNIPPET_CHARS as RESOURCE_DEFAULT_SNIPPET_CHARS
from clawops.hypermemory.defaults import (
    DEFAULT_WRITABLE_SCOPE_PATTERNS as RESOURCE_DEFAULT_WRITABLE_SCOPE_PATTERNS,
)
from clawops.hypermemory.defaults import (
    EMBEDDING_DEFAULTS,
    FACT_REGISTRY_DEFAULTS,
    FEEDBACK_DEFAULTS,
    HYBRID_DEFAULTS,
    INJECTION_DEFAULTS,
    NOISE_DEFAULTS,
    QDRANT_DEFAULTS,
    RANKING_DEFAULTS,
    RERANK_DEFAULTS,
    RERANK_HTTP_DEFAULTS,
    RERANK_LOCAL_DEFAULTS,
    RETRIEVAL_DEFAULTS,
    SEARCH_TYPE_WEIGHTS,
)

Lane = Literal["memory", "corpus"]
SearchMode = Literal["all", "memory", "corpus"]
SearchBackend = Literal["sqlite_fts", "qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"]
FusionMode = Literal["rrf", "weighted"]
EmbeddingProviderKind = Literal["disabled", "compatible-http"]
RerankProviderKind = Literal["none", "local-sentence-transformers", "compatible-http"]
EvidenceKind = Literal["file", "lcm_summary", "lcm_message_range", "external_uri"]
Tier = Literal["core", "working", "peripheral"]
CaptureMode = Literal["llm", "regex", "both"]
FactCategory = Literal["profile", "preference", "decision", "entity"]

DEFAULT_MEMORY_FILE_NAMES = RESOURCE_DEFAULT_MEMORY_FILE_NAMES
DEFAULT_DAILY_DIR = RESOURCE_DEFAULT_DAILY_DIR
DEFAULT_BANK_DIR = RESOURCE_DEFAULT_BANK_DIR
DEFAULT_DB_PATH = RESOURCE_DEFAULT_DB_PATH
DEFAULT_SNIPPET_CHARS = RESOURCE_DEFAULT_SNIPPET_CHARS
DEFAULT_SEARCH_RESULTS = RESOURCE_DEFAULT_SEARCH_RESULTS
DEFAULT_DEFAULT_SCOPE = RESOURCE_DEFAULT_DEFAULT_SCOPE
DEFAULT_READABLE_SCOPE_PATTERNS = RESOURCE_DEFAULT_READABLE_SCOPE_PATTERNS
DEFAULT_WRITABLE_SCOPE_PATTERNS = RESOURCE_DEFAULT_WRITABLE_SCOPE_PATTERNS
DEFAULT_AUTO_APPLY_SCOPE_PATTERNS = RESOURCE_DEFAULT_AUTO_APPLY_SCOPE_PATTERNS
DEFAULT_SEARCH_BACKEND: SearchBackend = DEFAULT_SEARCH_BACKEND_LITERAL
DEFAULT_FALLBACK_BACKEND: SearchBackend = DEFAULT_FALLBACK_BACKEND_LITERAL
DEFAULT_EMBEDDING_PROVIDER: EmbeddingProviderKind = DEFAULT_EMBEDDING_PROVIDER_LITERAL
DEFAULT_RERANK_PROVIDER: RerankProviderKind = DEFAULT_RERANK_PROVIDER_LITERAL
DEFAULT_QDRANT_URL = RESOURCE_DEFAULT_QDRANT_URL
DEFAULT_QDRANT_COLLECTION = RESOURCE_DEFAULT_QDRANT_COLLECTION
DEFAULT_QDRANT_DENSE_VECTOR_NAME = RESOURCE_DEFAULT_QDRANT_DENSE_VECTOR_NAME
DEFAULT_QDRANT_SPARSE_VECTOR_NAME = RESOURCE_DEFAULT_QDRANT_SPARSE_VECTOR_NAME
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


def _mapping_bool(section: Mapping[str, object], key: str) -> bool:
    """Return a validated boolean default from *section*."""
    return cast(bool, section[key])


def _mapping_int(section: Mapping[str, object], key: str) -> int:
    """Return a validated integer default from *section*."""
    return cast(int, section[key])


def _mapping_float(section: Mapping[str, object], key: str) -> float:
    """Return a validated numeric default from *section*."""
    return cast(float, section[key])


def _mapping_string(section: Mapping[str, object], key: str) -> str:
    """Return a validated string default from *section*."""
    return cast(str, section[key])


def _mapping_optional_string(section: Mapping[str, object], key: str) -> str | None:
    """Return a validated optional string default from *section*."""
    return cast(str | None, section[key])


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusPathConfig:
    """Additional Markdown corpus path to index."""

    name: str
    path: pathlib.Path
    pattern: str
    required: bool = False


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

    memory_lane_weight: float = _mapping_float(RANKING_DEFAULTS, "memory_lane_weight")
    corpus_lane_weight: float = _mapping_float(RANKING_DEFAULTS, "corpus_lane_weight")
    lexical_weight: float = _mapping_float(RANKING_DEFAULTS, "lexical_weight")
    coverage_weight: float = _mapping_float(RANKING_DEFAULTS, "coverage_weight")
    confidence_weight: float = _mapping_float(RANKING_DEFAULTS, "confidence_weight")
    recency_weight: float = _mapping_float(RANKING_DEFAULTS, "recency_weight")
    contradiction_penalty: float = _mapping_float(RANKING_DEFAULTS, "contradiction_penalty")
    diversity_penalty: float = _mapping_float(RANKING_DEFAULTS, "diversity_penalty")
    recency_half_life_days: int = _mapping_int(RANKING_DEFAULTS, "recency_half_life_days")
    rerank_weight: float = _mapping_float(RANKING_DEFAULTS, "rerank_weight")


@dataclasses.dataclass(frozen=True, slots=True)
class DedupConfig:
    """Semantic and deterministic deduplication settings."""

    enabled: bool = _mapping_bool(DEDUP_DEFAULTS, "enabled")
    similarity_threshold: float = _mapping_float(DEDUP_DEFAULTS, "similarity_threshold")
    check_cross_scope: bool = _mapping_bool(DEDUP_DEFAULTS, "check_cross_scope")
    typed_slots_enabled: bool = _mapping_bool(DEDUP_DEFAULTS, "typed_slots_enabled")
    llm_assisted_enabled: bool = _mapping_bool(DEDUP_DEFAULTS, "llm_assisted_enabled")
    llm_near_threshold: float = _mapping_float(DEDUP_DEFAULTS, "llm_near_threshold")


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureLlmConfig:
    """LLM-backed capture extraction settings."""

    endpoint: str = _mapping_string(CAPTURE_LLM_DEFAULTS, "endpoint")
    model: str = _mapping_string(CAPTURE_LLM_DEFAULTS, "model")
    api_key_env: str | None = _mapping_optional_string(CAPTURE_LLM_DEFAULTS, "api_key_env")
    api_key: str | None = _mapping_optional_string(CAPTURE_LLM_DEFAULTS, "api_key")
    timeout_ms: int = _mapping_int(CAPTURE_LLM_DEFAULTS, "timeout_ms")


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Conversation capture configuration."""

    enabled: bool = _mapping_bool(CAPTURE_DEFAULTS, "enabled")
    mode: CaptureMode = cast(CaptureMode, _mapping_string(CAPTURE_DEFAULTS, "mode"))
    min_message_length: int = _mapping_int(CAPTURE_DEFAULTS, "min_message_length")
    max_candidates_per_session: int = _mapping_int(CAPTURE_DEFAULTS, "max_candidates_per_session")
    incremental: bool = _mapping_bool(CAPTURE_DEFAULTS, "incremental")
    batch_size: int = _mapping_int(CAPTURE_DEFAULTS, "batch_size")
    batch_overlap: int = _mapping_int(CAPTURE_DEFAULTS, "batch_overlap")
    llm: CaptureLlmConfig = dataclasses.field(default_factory=CaptureLlmConfig)


@dataclasses.dataclass(frozen=True, slots=True)
class DecayConfig:
    """Decay scoring and tier-transition configuration."""

    enabled: bool = _mapping_bool(DECAY_DEFAULTS, "enabled")
    half_life_days: float = _mapping_float(DECAY_DEFAULTS, "half_life_days")
    recency_weight: float = _mapping_float(DECAY_DEFAULTS, "recency_weight")
    frequency_weight: float = _mapping_float(DECAY_DEFAULTS, "frequency_weight")
    intrinsic_weight: float = _mapping_float(DECAY_DEFAULTS, "intrinsic_weight")
    beta_core: float = _mapping_float(DECAY_DEFAULTS, "beta_core")
    beta_working: float = _mapping_float(DECAY_DEFAULTS, "beta_working")
    beta_peripheral: float = _mapping_float(DECAY_DEFAULTS, "beta_peripheral")
    promote_to_core_access: int = _mapping_int(DECAY_DEFAULTS, "promote_to_core_access")
    promote_to_core_composite: float = _mapping_float(DECAY_DEFAULTS, "promote_to_core_composite")
    promote_to_core_importance: float = _mapping_float(DECAY_DEFAULTS, "promote_to_core_importance")
    promote_to_working_access: int = _mapping_int(DECAY_DEFAULTS, "promote_to_working_access")
    promote_to_working_composite: float = _mapping_float(
        DECAY_DEFAULTS, "promote_to_working_composite"
    )
    demote_to_peripheral_composite: float = _mapping_float(
        DECAY_DEFAULTS, "demote_to_peripheral_composite"
    )
    demote_to_peripheral_age_days: int = _mapping_int(
        DECAY_DEFAULTS, "demote_to_peripheral_age_days"
    )
    demote_to_peripheral_access: int = _mapping_int(DECAY_DEFAULTS, "demote_to_peripheral_access")
    demote_from_core_composite: float = _mapping_float(DECAY_DEFAULTS, "demote_from_core_composite")
    demote_from_core_access: int = _mapping_int(DECAY_DEFAULTS, "demote_from_core_access")


@dataclasses.dataclass(frozen=True, slots=True)
class NoiseConfig:
    """Noise filtering thresholds for writes and capture."""

    enabled: bool = _mapping_bool(NOISE_DEFAULTS, "enabled")
    min_text_length: int = _mapping_int(NOISE_DEFAULTS, "min_text_length")
    max_text_length: int = _mapping_int(NOISE_DEFAULTS, "max_text_length")


@dataclasses.dataclass(frozen=True, slots=True)
class AdmissionConfig:
    """Optional capture-only admission control."""

    enabled: bool = _mapping_bool(ADMISSION_DEFAULTS, "enabled")
    type_priors: Mapping[str, float] = dataclasses.field(
        default_factory=lambda: dict(ADMISSION_TYPE_PRIORS_DEFAULTS)
    )
    min_confidence: float = _mapping_float(ADMISSION_DEFAULTS, "min_confidence")


@dataclasses.dataclass(frozen=True, slots=True)
class FactRegistryConfig:
    """Canonical fact registry behavior."""

    enabled: bool = _mapping_bool(FACT_REGISTRY_DEFAULTS, "enabled")
    auto_infer_keys: bool = _mapping_bool(FACT_REGISTRY_DEFAULTS, "auto_infer_keys")


@dataclasses.dataclass(frozen=True, slots=True)
class FeedbackConfig:
    """Feedback-signal scoring configuration."""

    enabled: bool = _mapping_bool(FEEDBACK_DEFAULTS, "enabled")
    reward_weight: float = _mapping_float(FEEDBACK_DEFAULTS, "reward_weight")
    penalty_weight: float = _mapping_float(FEEDBACK_DEFAULTS, "penalty_weight")
    suppress_threshold: int = _mapping_int(FEEDBACK_DEFAULTS, "suppress_threshold")
    suppress_penalty: float = _mapping_float(FEEDBACK_DEFAULTS, "suppress_penalty")


@dataclasses.dataclass(frozen=True, slots=True)
class RetrievalExtensionsConfig:
    """Retrieval behavior extensions outside the hybrid core."""

    adaptive_pool: bool = _mapping_bool(RETRIEVAL_DEFAULTS, "adaptive_pool")
    adaptive_pool_max_multiplier: int = _mapping_int(
        RETRIEVAL_DEFAULTS, "adaptive_pool_max_multiplier"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class BackendConfig:
    """Active and fallback search backend settings."""

    active: SearchBackend = cast(SearchBackend, _mapping_string(BACKEND_DEFAULTS, "active"))
    fallback: SearchBackend = cast(SearchBackend, _mapping_string(BACKEND_DEFAULTS, "fallback"))


@dataclasses.dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Embedding provider configuration."""

    enabled: bool = _mapping_bool(EMBEDDING_DEFAULTS, "enabled")
    provider: EmbeddingProviderKind = cast(
        EmbeddingProviderKind, _mapping_string(EMBEDDING_DEFAULTS, "provider")
    )
    model: str = _mapping_string(EMBEDDING_DEFAULTS, "model")
    base_url: str = _mapping_string(EMBEDDING_DEFAULTS, "base_url")
    api_key_env: str | None = _mapping_optional_string(EMBEDDING_DEFAULTS, "api_key_env")
    api_key: str | None = _mapping_optional_string(EMBEDDING_DEFAULTS, "api_key")
    dimensions: int | None = None
    batch_size: int = _mapping_int(EMBEDDING_DEFAULTS, "batch_size")
    timeout_ms: int = _mapping_int(EMBEDDING_DEFAULTS, "timeout_ms")


@dataclasses.dataclass(frozen=True, slots=True)
class LocalSentenceTransformersRerankConfig:
    """Local sentence-transformers rerank provider configuration."""

    model: str = _mapping_string(RERANK_LOCAL_DEFAULTS, "model")
    batch_size: int = _mapping_int(RERANK_LOCAL_DEFAULTS, "batch_size")
    max_length: int = _mapping_int(RERANK_LOCAL_DEFAULTS, "max_length")
    device: str = _mapping_string(RERANK_LOCAL_DEFAULTS, "device")


@dataclasses.dataclass(frozen=True, slots=True)
class CompatibleHttpRerankConfig:
    """Compatible HTTP rerank provider configuration."""

    model: str = _mapping_string(RERANK_HTTP_DEFAULTS, "model")
    base_url: str = _mapping_string(RERANK_HTTP_DEFAULTS, "base_url")
    api_key_env: str | None = _mapping_optional_string(RERANK_HTTP_DEFAULTS, "api_key_env")
    api_key: str | None = _mapping_optional_string(RERANK_HTTP_DEFAULTS, "api_key")
    timeout_ms: int = _mapping_int(RERANK_HTTP_DEFAULTS, "timeout_ms")


@dataclasses.dataclass(frozen=True, slots=True)
class RerankConfig:
    """Optional reranking configuration with primary and fallback providers."""

    enabled: bool = _mapping_bool(RERANK_DEFAULTS, "enabled")
    provider: RerankProviderKind = cast(
        RerankProviderKind, _mapping_string(RERANK_DEFAULTS, "provider")
    )
    fallback_provider: RerankProviderKind = cast(
        RerankProviderKind, _mapping_string(RERANK_DEFAULTS, "fallback_provider")
    )
    fail_open: bool = _mapping_bool(RERANK_DEFAULTS, "fail_open")
    normalize_scores: bool = _mapping_bool(RERANK_DEFAULTS, "normalize_scores")
    local: LocalSentenceTransformersRerankConfig = dataclasses.field(
        default_factory=LocalSentenceTransformersRerankConfig
    )
    compatible_http: CompatibleHttpRerankConfig = dataclasses.field(
        default_factory=CompatibleHttpRerankConfig
    )

    def model_for(self, provider: RerankProviderKind | None = None) -> str:
        """Return the configured model name for *provider*."""
        resolved_provider = self.provider if provider is None else provider
        if resolved_provider == "local-sentence-transformers":
            return self.local.model
        if resolved_provider == "compatible-http":
            return self.compatible_http.model
        return ""


@dataclasses.dataclass(frozen=True, slots=True)
class HybridConfig:
    """Hybrid retrieval configuration."""

    dense_candidate_pool: int = _mapping_int(HYBRID_DEFAULTS, "dense_candidate_pool")
    sparse_candidate_pool: int = _mapping_int(HYBRID_DEFAULTS, "sparse_candidate_pool")
    vector_weight: float = _mapping_float(HYBRID_DEFAULTS, "vector_weight")
    text_weight: float = _mapping_float(HYBRID_DEFAULTS, "text_weight")
    fusion: FusionMode = cast(FusionMode, _mapping_string(HYBRID_DEFAULTS, "fusion"))
    rrf_k: int = _mapping_int(HYBRID_DEFAULTS, "rrf_k")
    rerank_candidate_pool: int = _mapping_int(HYBRID_DEFAULTS, "rerank_candidate_pool")


@dataclasses.dataclass(frozen=True, slots=True)
class QdrantConfig:
    """Dense and sparse Qdrant backend configuration."""

    enabled: bool = _mapping_bool(QDRANT_DEFAULTS, "enabled")
    url: str = _mapping_string(QDRANT_DEFAULTS, "url")
    collection: str = _mapping_string(QDRANT_DEFAULTS, "collection")
    dense_vector_name: str = _mapping_string(QDRANT_DEFAULTS, "dense_vector_name")
    sparse_vector_name: str = _mapping_string(QDRANT_DEFAULTS, "sparse_vector_name")
    timeout_ms: int = _mapping_int(QDRANT_DEFAULTS, "timeout_ms")
    api_key_env: str | None = _mapping_optional_string(QDRANT_DEFAULTS, "api_key_env")
    api_key: str | None = _mapping_optional_string(QDRANT_DEFAULTS, "api_key")


@dataclasses.dataclass(frozen=True, slots=True)
class InjectionConfig:
    """Prompt injection caps for auto-recall."""

    max_results: int = _mapping_int(INJECTION_DEFAULTS, "max_results")
    max_chars_per_result: int = _mapping_int(INJECTION_DEFAULTS, "max_chars_per_result")


@dataclasses.dataclass(frozen=True, slots=True)
class HypermemoryConfig:
    """Validated hypermemory configuration."""

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
    dedup: DedupConfig = dataclasses.field(default_factory=DedupConfig)
    capture: CaptureConfig = dataclasses.field(default_factory=CaptureConfig)
    decay: DecayConfig = dataclasses.field(default_factory=DecayConfig)
    noise: NoiseConfig = dataclasses.field(default_factory=NoiseConfig)
    admission: AdmissionConfig = dataclasses.field(default_factory=AdmissionConfig)
    fact_registry: FactRegistryConfig = dataclasses.field(default_factory=FactRegistryConfig)
    feedback: FeedbackConfig = dataclasses.field(default_factory=FeedbackConfig)
    retrieval: RetrievalExtensionsConfig = dataclasses.field(
        default_factory=RetrievalExtensionsConfig
    )

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
    importance: float | None = None
    tier: Tier = "working"
    access_count: int = 0
    last_access_date: str | None = None
    injected_count: int = 0
    confirmed_count: int = 0
    bad_recall_count: int = 0
    fact_key: str | None = None
    invalidated_at: str | None = None
    supersedes: str | None = None


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
    rerank_score: float = 0.0
    novelty_penalty: float = 0.0
    decay_boost: float = 0.0
    feedback_boost: float = 0.0
    feedback_penalty: float = 0.0

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
            "rerankScore": round(self.rerank_score, 6),
            "noveltyPenalty": round(self.novelty_penalty, 6),
            "decayBoost": round(self.decay_boost, 6),
            "feedbackBoost": round(self.feedback_boost, 6),
            "feedbackPenalty": round(self.feedback_penalty, 6),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DenseSearchCandidate:
    """Dense search result from the vector backend."""

    item_id: int
    point_id: str
    score: float


@dataclasses.dataclass(frozen=True, slots=True)
class SparseSearchCandidate:
    """Sparse search result from the vector backend."""

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
    qdrant_dense_ms: float = 0.0
    qdrant_sparse_ms: float = 0.0
    fusion_ms: float = 0.0
    rerank_ms: float = 0.0
    lexical_candidates: int = 0
    sparse_candidates: int = 0
    dense_candidates: int = 0
    rerank_candidates: int = 0
    selected_candidates: int = 0
    rerank_applied: bool = False
    rerank_fallback_used: bool = False
    rerank_fail_open: bool = False
    rerank_provider: str = "none"

    def to_dict(self) -> dict[str, float | int | bool | str]:
        """Convert diagnostics to telemetry-friendly scalars."""
        return {
            "lexicalMs": round(self.lexical_ms, 3),
            "sqliteDenseMs": round(self.sqlite_dense_ms, 3),
            "qdrantDenseSearchMs": round(self.qdrant_dense_ms, 3),
            "qdrantSparseSearchMs": round(self.qdrant_sparse_ms, 3),
            "fusionMs": round(self.fusion_ms, 3),
            "rerankMs": round(self.rerank_ms, 3),
            "lexicalCandidates": self.lexical_candidates,
            "sparseCandidates": self.sparse_candidates,
            "denseCandidates": self.dense_candidates,
            "rerankCandidates": self.rerank_candidates,
            "selectedCandidates": self.selected_candidates,
            "rerankApplied": self.rerank_applied,
            "rerankFallbackUsed": self.rerank_fallback_used,
            "rerankFailOpen": self.rerank_fail_open,
            "rerankProvider": self.rerank_provider,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RerankResponse:
    """Normalized planner-time rerank response."""

    scores: tuple[float, ...] = ()
    provider: RerankProviderKind = "none"
    applied: bool = False
    fallback_used: bool = False
    latency_ms: float = 0.0
    fail_open: bool = False
    error: str | None = None


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
    item_id: int | None = None
    importance: float | None = None
    tier: Tier = "working"
    access_count: int = 0
    last_access_date: str | None = None
    injected_count: int = 0
    confirmed_count: int = 0
    bad_recall_count: int = 0
    fact_key: str | None = None
    invalidated_at: str | None = None
    supersedes: str | None = None

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
            "tier": self.tier,
            "accessCount": self.access_count,
            "injectedCount": self.injected_count,
            "confirmedCount": self.confirmed_count,
            "badRecallCount": self.bad_recall_count,
        }
        if self.item_id is not None:
            payload["itemId"] = self.item_id
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.entities:
            payload["entities"] = list(self.entities)
        if self.scope:
            payload["scope"] = self.scope
        if self.importance is not None:
            payload["importance"] = self.importance
        if self.last_access_date is not None:
            payload["lastAccess"] = self.last_access_date
        if self.fact_key is not None:
            payload["factKey"] = self.fact_key
        if self.invalidated_at is not None:
            payload["invalidatedAt"] = self.invalidated_at
        if self.supersedes is not None:
            payload["supersedes"] = self.supersedes
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


TYPE_ORDER: dict[EntryType, float] = cast(dict[EntryType, float], dict(SEARCH_TYPE_WEIGHTS))


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
