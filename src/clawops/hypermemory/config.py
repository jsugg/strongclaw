"""Configuration loading for StrongClaw hypermemory."""

from __future__ import annotations

import fnmatch
import os
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any, cast

from clawops.common import load_yaml
from clawops.hypermemory.governance import validate_scope
from clawops.hypermemory.models import (
    DEFAULT_AUTO_APPLY_SCOPE_PATTERNS,
    DEFAULT_BANK_DIR,
    DEFAULT_DAILY_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_DEFAULT_SCOPE,
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_FALLBACK_BACKEND,
    DEFAULT_MEMORY_FILE_NAMES,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_DENSE_VECTOR_NAME,
    DEFAULT_QDRANT_SPARSE_VECTOR_NAME,
    DEFAULT_QDRANT_URL,
    DEFAULT_READABLE_SCOPE_PATTERNS,
    DEFAULT_RERANK_PROVIDER,
    DEFAULT_SEARCH_BACKEND,
    DEFAULT_SEARCH_RESULTS,
    DEFAULT_SNIPPET_CHARS,
    DEFAULT_WRITABLE_SCOPE_PATTERNS,
    AdmissionConfig,
    BackendConfig,
    CaptureConfig,
    CaptureLlmConfig,
    CompatibleHttpRerankConfig,
    CorpusPathConfig,
    DecayConfig,
    DedupConfig,
    EmbeddingConfig,
    EmbeddingProviderKind,
    FactRegistryConfig,
    FeedbackConfig,
    FusionMode,
    GovernanceConfig,
    HybridConfig,
    HypermemoryConfig,
    InjectionConfig,
    LocalSentenceTransformersRerankConfig,
    NoiseConfig,
    QdrantConfig,
    RankingConfig,
    RerankConfig,
    RerankProviderKind,
    RetrievalExtensionsConfig,
    SearchBackend,
)


def _as_mapping(name: str, value: object) -> Mapping[str, object]:
    """Validate a mapping-shaped configuration section."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _resolve_env_reference(value: object) -> object:
    """Resolve `os.environ/KEY` references inside string configuration values."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith("os.environ/"):
        return stripped
    env_key = stripped.removeprefix("os.environ/").strip()
    if not env_key:
        raise ValueError("environment-backed config reference must name a variable")
    return os.environ.get(env_key, "").strip()


def _as_string(name: str, value: object, *, default: str | None = None) -> str:
    """Validate a string configuration value."""
    resolved = _resolve_env_reference(value)
    if resolved is None or resolved == "":
        if default is None:
            raise TypeError(f"{name} must be a string")
        return default
    if not isinstance(resolved, str):
        raise TypeError(f"{name} must be a non-empty string")
    return resolved


def _as_bool(name: str, value: object, *, default: bool) -> bool:
    """Validate a boolean configuration value."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _as_optional_string(name: str, value: object) -> str | None:
    """Validate an optional string configuration value."""
    resolved = _resolve_env_reference(value)
    if resolved is None or resolved == "":
        return None
    if not isinstance(resolved, str):
        raise TypeError(f"{name} must be a non-empty string when provided")
    return resolved


def _as_blankable_string(name: str, value: object, *, default: str = "") -> str:
    """Validate a string configuration value that may be blank."""
    resolved = _resolve_env_reference(value)
    if resolved is None or resolved == "":
        return default
    if not isinstance(resolved, str):
        raise TypeError(f"{name} must be a string")
    return resolved


def _as_positive_int(name: str, value: object, *, default: int) -> int:
    """Validate a positive integer configuration value."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _as_positive_float(name: str, value: object, *, default: float) -> float:
    """Validate a positive float configuration value."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    converted = float(value)
    if converted <= 0:
        raise ValueError(f"{name} must be positive")
    return converted


def _as_probability(name: str, value: object, *, default: float) -> float:
    """Validate a floating-point probability in the closed unit interval."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    converted = float(value)
    if converted < 0.0 or converted > 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")
    return converted


def _as_non_negative_int(name: str, value: object, *, default: int) -> int:
    """Validate a non-negative integer configuration value."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be zero or positive")
    return value


def _as_string_list(name: str, value: object, *, default: Sequence[str]) -> tuple[str, ...]:
    """Validate a list of non-empty strings."""
    if value is None:
        return tuple(default)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise TypeError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _resolve_path(base_dir: pathlib.Path, raw_path: str) -> pathlib.Path:
    """Resolve a config path relative to *base_dir*."""
    path = pathlib.Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def resolve_under_workspace(workspace_root: pathlib.Path, path: pathlib.Path) -> str:
    """Return *path* relative to the workspace root."""
    try:
        return path.resolve().relative_to(workspace_root).as_posix()
    except ValueError as err:
        raise ValueError(f"{path} must stay within {workspace_root}") from err


def matches_glob(path_text: str, pattern: str) -> bool:
    """Match a relative path against a repo-style glob."""
    return fnmatch.fnmatch(path_text, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(path_text, pattern[3:])
    )


def default_config_path() -> pathlib.Path:
    """Return the shipped default hypermemory config path."""
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    return repo_root / "platform/configs/memory/hypermemory.sqlite.yaml"


def _load_governance(root: Mapping[str, object]) -> GovernanceConfig:
    """Load governance config with backward-compatible defaults."""
    governance = _as_mapping("governance", root.get("governance") or {})
    default_scope = validate_scope(
        _as_string(
            "governance.default_scope",
            governance.get("default_scope"),
            default=DEFAULT_DEFAULT_SCOPE,
        )
    )
    readable = tuple(
        (
            validate_scope("global" if pattern == "global" else pattern.rstrip(":") + ":scope")[:-5]
            if pattern.endswith(":")
            else validate_scope(pattern)
        )
        for pattern in _as_string_list(
            "governance.readable_scopes",
            governance.get("readable_scopes"),
            default=DEFAULT_READABLE_SCOPE_PATTERNS,
        )
    )
    writable = tuple(
        (
            validate_scope("global" if pattern == "global" else pattern.rstrip(":") + ":scope")[:-5]
            if pattern.endswith(":")
            else validate_scope(pattern)
        )
        for pattern in _as_string_list(
            "governance.writable_scopes",
            governance.get("writable_scopes"),
            default=DEFAULT_WRITABLE_SCOPE_PATTERNS,
        )
    )
    auto_apply = tuple(
        (
            validate_scope("global" if pattern == "global" else pattern.rstrip(":") + ":scope")[:-5]
            if pattern.endswith(":")
            else validate_scope(pattern)
        )
        for pattern in _as_string_list(
            "governance.auto_apply_scopes",
            governance.get("auto_apply_scopes"),
            default=DEFAULT_AUTO_APPLY_SCOPE_PATTERNS,
        )
    )
    return GovernanceConfig(
        default_scope=default_scope,
        readable_scope_patterns=readable,
        writable_scope_patterns=writable,
        auto_apply_scope_patterns=auto_apply,
    )


def _load_ranking(root: Mapping[str, object]) -> RankingConfig:
    """Load ranking config with backward-compatible defaults."""
    ranking = _as_mapping("ranking", root.get("ranking") or {})
    return RankingConfig(
        memory_lane_weight=_as_positive_float(
            "ranking.memory_lane_weight", ranking.get("memory_lane_weight"), default=1.0
        ),
        corpus_lane_weight=_as_positive_float(
            "ranking.corpus_lane_weight", ranking.get("corpus_lane_weight"), default=1.0
        ),
        lexical_weight=_as_positive_float(
            "ranking.lexical_weight", ranking.get("lexical_weight"), default=0.75
        ),
        coverage_weight=_as_positive_float(
            "ranking.coverage_weight", ranking.get("coverage_weight"), default=0.35
        ),
        confidence_weight=_as_positive_float(
            "ranking.confidence_weight", ranking.get("confidence_weight"), default=0.15
        ),
        recency_weight=_as_positive_float(
            "ranking.recency_weight", ranking.get("recency_weight"), default=0.1
        ),
        contradiction_penalty=_as_positive_float(
            "ranking.contradiction_penalty",
            ranking.get("contradiction_penalty"),
            default=0.2,
        ),
        diversity_penalty=_as_positive_float(
            "ranking.diversity_penalty", ranking.get("diversity_penalty"), default=0.35
        ),
        recency_half_life_days=_as_positive_int(
            "ranking.recency_half_life_days",
            ranking.get("recency_half_life_days"),
            default=45,
        ),
        rerank_weight=_as_probability(
            "ranking.rerank_weight",
            ranking.get("rerank_weight"),
            default=0.35,
        ),
    )


def _as_search_backend(name: str, value: object, *, default: SearchBackend) -> SearchBackend:
    """Validate a configured search backend."""
    backend = _as_string(name, value, default=default)
    if backend not in {"sqlite_fts", "qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"}:
        raise ValueError(
            f"{name} must be sqlite_fts, qdrant_dense_hybrid, or qdrant_sparse_dense_hybrid"
        )
    return cast(SearchBackend, backend)


def _as_embedding_provider(
    name: str,
    value: object,
    *,
    default: EmbeddingProviderKind,
) -> EmbeddingProviderKind:
    """Validate an embedding provider identifier."""
    provider = _as_string(name, value, default=default)
    if provider not in {"disabled", "compatible-http"}:
        raise ValueError(f"{name} must be disabled or compatible-http")
    return cast(EmbeddingProviderKind, provider)


def _as_fusion_mode(name: str, value: object, *, default: FusionMode) -> FusionMode:
    """Validate a fusion mode."""
    fusion = _as_string(name, value, default=default)
    if fusion not in {"rrf", "weighted"}:
        raise ValueError(f"{name} must be rrf or weighted")
    return cast(FusionMode, fusion)


def _as_capture_mode(name: str, value: object, *, default: str) -> str:
    """Validate a capture mode."""
    mode = _as_string(name, value, default=default)
    if mode not in {"llm", "regex", "both"}:
        raise ValueError(f"{name} must be llm, regex, or both")
    return mode


def _as_rerank_provider(
    name: str,
    value: object,
    *,
    default: RerankProviderKind,
) -> RerankProviderKind:
    """Validate a rerank provider identifier."""
    provider = _as_string(name, value, default=default)
    if provider not in {"none", "local-sentence-transformers", "compatible-http"}:
        raise ValueError(f"{name} must be none, local-sentence-transformers, or compatible-http")
    return cast(RerankProviderKind, provider)


def _load_backend(root: Mapping[str, object]) -> BackendConfig:
    """Load backend configuration."""
    backend = _as_mapping("backend", root.get("backend") or {})
    return BackendConfig(
        active=_as_search_backend(
            "backend.active",
            backend.get("active"),
            default=cast(SearchBackend, DEFAULT_SEARCH_BACKEND),
        ),
        fallback=_as_search_backend(
            "backend.fallback",
            backend.get("fallback"),
            default=cast(SearchBackend, DEFAULT_FALLBACK_BACKEND),
        ),
    )


def _load_embedding(root: Mapping[str, object]) -> EmbeddingConfig:
    """Load embedding provider configuration."""
    embedding = _as_mapping("embedding", root.get("embedding") or {})
    return EmbeddingConfig(
        enabled=_as_bool("embedding.enabled", embedding.get("enabled"), default=False),
        provider=_as_embedding_provider(
            "embedding.provider",
            embedding.get("provider"),
            default=cast(EmbeddingProviderKind, DEFAULT_EMBEDDING_PROVIDER),
        ),
        model=_as_blankable_string("embedding.model", embedding.get("model")),
        base_url=_as_blankable_string("embedding.base_url", embedding.get("base_url")),
        api_key_env=_as_optional_string("embedding.api_key_env", embedding.get("api_key_env")),
        api_key=_as_optional_string("embedding.api_key", embedding.get("api_key")),
        dimensions=(
            _as_positive_int("embedding.dimensions", embedding.get("dimensions"), default=1)
            if embedding.get("dimensions") is not None
            else None
        ),
        batch_size=_as_positive_int(
            "embedding.batch_size", embedding.get("batch_size"), default=32
        ),
        timeout_ms=_as_positive_int(
            "embedding.timeout_ms", embedding.get("timeout_ms"), default=15_000
        ),
    )


def _load_local_rerank(
    rerank: Mapping[str, object],
    provider: RerankProviderKind,
    fallback_provider: RerankProviderKind,
) -> LocalSentenceTransformersRerankConfig:
    """Load the local sentence-transformers rerank subsection."""
    local = _as_mapping("rerank.local", rerank.get("local") or {})
    legacy_model = rerank.get("model") if provider == "local-sentence-transformers" else None
    return LocalSentenceTransformersRerankConfig(
        model=_as_blankable_string(
            "rerank.local.model",
            local.get("model", legacy_model),
        ),
        batch_size=_as_positive_int(
            "rerank.local.batch_size",
            local.get("batch_size", rerank.get("batch_size")),
            default=8,
        ),
        max_length=_as_positive_int(
            "rerank.local.max_length",
            local.get("max_length", rerank.get("max_length")),
            default=2_048,
        ),
        device=_as_blankable_string(
            "rerank.local.device",
            local.get("device", rerank.get("device")),
            default="auto",
        ),
    )


def _load_compatible_http_rerank(
    rerank: Mapping[str, object],
    provider: RerankProviderKind,
    fallback_provider: RerankProviderKind,
) -> CompatibleHttpRerankConfig:
    """Load the compatible HTTP rerank subsection."""
    compatible_http = _as_mapping("rerank.compatible_http", rerank.get("compatible_http") or {})
    legacy_model = rerank.get("model") if provider == "compatible-http" else None
    legacy_base_url = rerank.get("base_url") if provider == "compatible-http" else None
    uses_compatible_http = provider == "compatible-http" or fallback_provider == "compatible-http"
    return CompatibleHttpRerankConfig(
        model=_as_blankable_string(
            "rerank.compatible_http.model",
            compatible_http.get("model", legacy_model),
        ),
        base_url=_as_blankable_string(
            "rerank.compatible_http.base_url",
            compatible_http.get("base_url", legacy_base_url),
        ),
        api_key_env=_as_optional_string(
            "rerank.compatible_http.api_key_env",
            compatible_http.get(
                "api_key_env", rerank.get("api_key_env") if uses_compatible_http else None
            ),
        ),
        api_key=_as_optional_string(
            "rerank.compatible_http.api_key",
            compatible_http.get("api_key", rerank.get("api_key") if uses_compatible_http else None),
        ),
        timeout_ms=_as_positive_int(
            "rerank.compatible_http.timeout_ms",
            compatible_http.get(
                "timeout_ms", rerank.get("timeout_ms") if uses_compatible_http else None
            ),
            default=15_000,
        ),
    )


def _load_rerank(root: Mapping[str, object]) -> RerankConfig:
    """Load reranking configuration."""
    rerank = _as_mapping("rerank", root.get("rerank") or {})
    provider = _as_rerank_provider(
        "rerank.provider",
        rerank.get("provider"),
        default=DEFAULT_RERANK_PROVIDER,
    )
    fallback_provider = _as_rerank_provider(
        "rerank.fallback_provider",
        rerank.get("fallback_provider"),
        default="none",
    )
    return RerankConfig(
        enabled=_as_bool("rerank.enabled", rerank.get("enabled"), default=False),
        provider=provider,
        fallback_provider=fallback_provider,
        fail_open=_as_bool("rerank.fail_open", rerank.get("fail_open"), default=True),
        normalize_scores=_as_bool(
            "rerank.normalize_scores",
            rerank.get("normalize_scores"),
            default=True,
        ),
        local=_load_local_rerank(rerank, provider, fallback_provider),
        compatible_http=_load_compatible_http_rerank(rerank, provider, fallback_provider),
    )


def _load_hybrid(root: Mapping[str, object]) -> HybridConfig:
    """Load hybrid retrieval configuration."""
    hybrid = _as_mapping("hybrid", root.get("hybrid") or {})
    rerank = _as_mapping("rerank", root.get("rerank") or {})
    rerank_candidate_pool_keys = {
        "hybrid.rerank_candidate_pool": hybrid.get("rerank_candidate_pool"),
        "hybrid.rerank_top_k": hybrid.get("rerank_top_k"),
        "rerank.top_k": rerank.get("top_k"),
    }
    explicit_rerank_candidate_pool = {
        name: _as_non_negative_int(name, value, default=0)
        for name, value in rerank_candidate_pool_keys.items()
        if value is not None
    }
    if len(set(explicit_rerank_candidate_pool.values())) > 1:
        configured = ", ".join(
            f"{name}={value}" for name, value in explicit_rerank_candidate_pool.items()
        )
        raise ValueError(
            "rerank candidate pool settings must agree when multiple keys are present: "
            f"{configured}"
        )
    rerank_candidate_pool = next(iter(explicit_rerank_candidate_pool.values()), 0)
    return HybridConfig(
        dense_candidate_pool=_as_positive_int(
            "hybrid.dense_candidate_pool",
            hybrid.get("dense_candidate_pool"),
            default=24,
        ),
        sparse_candidate_pool=_as_positive_int(
            "hybrid.sparse_candidate_pool",
            hybrid.get("sparse_candidate_pool"),
            default=24,
        ),
        vector_weight=_as_positive_float(
            "hybrid.vector_weight", hybrid.get("vector_weight"), default=0.65
        ),
        text_weight=_as_positive_float(
            "hybrid.text_weight", hybrid.get("text_weight"), default=0.35
        ),
        fusion=_as_fusion_mode("hybrid.fusion", hybrid.get("fusion"), default="rrf"),
        rrf_k=_as_positive_int("hybrid.rrf_k", hybrid.get("rrf_k"), default=60),
        rerank_candidate_pool=rerank_candidate_pool,
    )


def _load_qdrant(root: Mapping[str, object]) -> QdrantConfig:
    """Load Qdrant backend configuration."""
    qdrant = _as_mapping("qdrant", root.get("qdrant") or {})
    return QdrantConfig(
        enabled=_as_bool("qdrant.enabled", qdrant.get("enabled"), default=False),
        url=_as_string("qdrant.url", qdrant.get("url"), default=DEFAULT_QDRANT_URL),
        collection=_as_string(
            "qdrant.collection",
            qdrant.get("collection"),
            default=DEFAULT_QDRANT_COLLECTION,
        ),
        dense_vector_name=_as_string(
            "qdrant.dense_vector_name",
            qdrant.get("dense_vector_name"),
            default=DEFAULT_QDRANT_DENSE_VECTOR_NAME,
        ),
        sparse_vector_name=_as_string(
            "qdrant.sparse_vector_name",
            qdrant.get("sparse_vector_name"),
            default=DEFAULT_QDRANT_SPARSE_VECTOR_NAME,
        ),
        timeout_ms=_as_positive_int("qdrant.timeout_ms", qdrant.get("timeout_ms"), default=3_000),
        api_key_env=_as_optional_string("qdrant.api_key_env", qdrant.get("api_key_env")),
        api_key=_as_optional_string("qdrant.api_key", qdrant.get("api_key")),
    )


def _load_injection(root: Mapping[str, object]) -> InjectionConfig:
    """Load recall injection configuration."""
    injection = _as_mapping("injection", root.get("injection") or {})
    return InjectionConfig(
        max_results=_as_positive_int(
            "injection.max_results", injection.get("max_results"), default=3
        ),
        max_chars_per_result=_as_positive_int(
            "injection.max_chars_per_result",
            injection.get("max_chars_per_result"),
            default=280,
        ),
    )


def _load_dedup(root: Mapping[str, object]) -> DedupConfig:
    """Load deduplication settings."""
    dedup = _as_mapping("dedup", root.get("dedup") or {})
    return DedupConfig(
        enabled=_as_bool("dedup.enabled", dedup.get("enabled"), default=False),
        similarity_threshold=_as_probability(
            "dedup.similarity_threshold",
            dedup.get("similarity_threshold"),
            default=0.92,
        ),
        check_cross_scope=_as_bool(
            "dedup.check_cross_scope",
            dedup.get("check_cross_scope"),
            default=False,
        ),
        typed_slots_enabled=_as_bool(
            "dedup.typed_slots_enabled",
            dedup.get("typed_slots_enabled"),
            default=True,
        ),
        llm_assisted_enabled=_as_bool(
            "dedup.llm_assisted_enabled",
            dedup.get("llm_assisted_enabled"),
            default=False,
        ),
        llm_near_threshold=_as_probability(
            "dedup.llm_near_threshold",
            dedup.get("llm_near_threshold"),
            default=0.85,
        ),
    )


def _load_capture(root: Mapping[str, object]) -> CaptureConfig:
    """Load conversation capture settings."""
    capture = _as_mapping("capture", root.get("capture") or {})
    llm = _as_mapping("capture.llm", capture.get("llm") or {})
    batch_size = _as_positive_int("capture.batch_size", capture.get("batch_size"), default=6)
    batch_overlap = _as_non_negative_int(
        "capture.batch_overlap",
        capture.get("batch_overlap"),
        default=2,
    )
    if batch_overlap >= batch_size:
        batch_overlap = max(batch_size - 1, 0)
    return CaptureConfig(
        enabled=_as_bool("capture.enabled", capture.get("enabled"), default=False),
        mode=cast(
            Any,
            _as_capture_mode("capture.mode", capture.get("mode"), default="llm"),
        ),
        min_message_length=_as_positive_int(
            "capture.min_message_length",
            capture.get("min_message_length"),
            default=20,
        ),
        max_candidates_per_session=_as_positive_int(
            "capture.max_candidates_per_session",
            capture.get("max_candidates_per_session"),
            default=10,
        ),
        incremental=_as_bool("capture.incremental", capture.get("incremental"), default=True),
        batch_size=batch_size,
        batch_overlap=batch_overlap,
        llm=CaptureLlmConfig(
            endpoint=_as_blankable_string("capture.llm.endpoint", llm.get("endpoint")),
            model=_as_blankable_string("capture.llm.model", llm.get("model")),
            api_key_env=_as_optional_string("capture.llm.api_key_env", llm.get("api_key_env")),
            api_key=_as_optional_string("capture.llm.api_key", llm.get("api_key")),
            timeout_ms=_as_positive_int(
                "capture.llm.timeout_ms",
                llm.get("timeout_ms"),
                default=15_000,
            ),
        ),
    )


def _load_decay(root: Mapping[str, object]) -> DecayConfig:
    """Load decay and tier-transition settings."""
    decay = _as_mapping("decay", root.get("decay") or {})
    return DecayConfig(
        enabled=_as_bool("decay.enabled", decay.get("enabled"), default=False),
        half_life_days=_as_positive_float(
            "decay.half_life_days",
            decay.get("half_life_days"),
            default=45.0,
        ),
        recency_weight=_as_probability(
            "decay.recency_weight",
            decay.get("recency_weight"),
            default=0.4,
        ),
        frequency_weight=_as_probability(
            "decay.frequency_weight",
            decay.get("frequency_weight"),
            default=0.3,
        ),
        intrinsic_weight=_as_probability(
            "decay.intrinsic_weight",
            decay.get("intrinsic_weight"),
            default=0.3,
        ),
        beta_core=_as_positive_float("decay.beta_core", decay.get("beta_core"), default=0.8),
        beta_working=_as_positive_float(
            "decay.beta_working",
            decay.get("beta_working"),
            default=1.0,
        ),
        beta_peripheral=_as_positive_float(
            "decay.beta_peripheral",
            decay.get("beta_peripheral"),
            default=1.3,
        ),
        promote_to_core_access=_as_non_negative_int(
            "decay.promote_to_core_access",
            decay.get("promote_to_core_access"),
            default=10,
        ),
        promote_to_core_composite=_as_probability(
            "decay.promote_to_core_composite",
            decay.get("promote_to_core_composite"),
            default=0.7,
        ),
        promote_to_core_importance=_as_probability(
            "decay.promote_to_core_importance",
            decay.get("promote_to_core_importance"),
            default=0.8,
        ),
        promote_to_working_access=_as_non_negative_int(
            "decay.promote_to_working_access",
            decay.get("promote_to_working_access"),
            default=3,
        ),
        promote_to_working_composite=_as_probability(
            "decay.promote_to_working_composite",
            decay.get("promote_to_working_composite"),
            default=0.4,
        ),
        demote_to_peripheral_composite=_as_probability(
            "decay.demote_to_peripheral_composite",
            decay.get("demote_to_peripheral_composite"),
            default=0.15,
        ),
        demote_to_peripheral_age_days=_as_positive_int(
            "decay.demote_to_peripheral_age_days",
            decay.get("demote_to_peripheral_age_days"),
            default=60,
        ),
        demote_to_peripheral_access=_as_non_negative_int(
            "decay.demote_to_peripheral_access",
            decay.get("demote_to_peripheral_access"),
            default=3,
        ),
        demote_from_core_composite=_as_probability(
            "decay.demote_from_core_composite",
            decay.get("demote_from_core_composite"),
            default=0.15,
        ),
        demote_from_core_access=_as_non_negative_int(
            "decay.demote_from_core_access",
            decay.get("demote_from_core_access"),
            default=3,
        ),
    )


def _load_noise(root: Mapping[str, object]) -> NoiseConfig:
    """Load noise filtering settings."""
    noise = _as_mapping("noise", root.get("noise") or {})
    return NoiseConfig(
        enabled=_as_bool("noise.enabled", noise.get("enabled"), default=True),
        min_text_length=_as_positive_int(
            "noise.min_text_length",
            noise.get("min_text_length"),
            default=10,
        ),
        max_text_length=_as_positive_int(
            "noise.max_text_length",
            noise.get("max_text_length"),
            default=2_000,
        ),
    )


def _load_admission(root: Mapping[str, object]) -> AdmissionConfig:
    """Load optional capture admission controls."""
    admission = _as_mapping("admission", root.get("admission") or {})
    priors_value = admission.get("type_priors") or {}
    priors_mapping = _as_mapping("admission.type_priors", priors_value)
    priors = {
        key: _as_probability(
            f"admission.type_priors.{key}",
            value,
            default=default,
        )
        for key, value, default in (
            ("fact", priors_mapping.get("fact"), 0.85),
            ("entity", priors_mapping.get("entity"), 0.80),
            ("opinion", priors_mapping.get("opinion"), 0.70),
            ("reflection", priors_mapping.get("reflection"), 0.75),
        )
    }
    return AdmissionConfig(
        enabled=_as_bool("admission.enabled", admission.get("enabled"), default=False),
        type_priors=priors,
        min_confidence=_as_probability(
            "admission.min_confidence",
            admission.get("min_confidence"),
            default=0.3,
        ),
    )


def _load_fact_registry(root: Mapping[str, object]) -> FactRegistryConfig:
    """Load canonical fact registry settings."""
    fact_registry = _as_mapping("fact_registry", root.get("fact_registry") or {})
    return FactRegistryConfig(
        enabled=_as_bool("fact_registry.enabled", fact_registry.get("enabled"), default=True),
        auto_infer_keys=_as_bool(
            "fact_registry.auto_infer_keys",
            fact_registry.get("auto_infer_keys"),
            default=True,
        ),
    )


def _load_feedback(root: Mapping[str, object]) -> FeedbackConfig:
    """Load feedback-signal settings."""
    feedback = _as_mapping("feedback", root.get("feedback") or {})
    return FeedbackConfig(
        enabled=_as_bool("feedback.enabled", feedback.get("enabled"), default=False),
        reward_weight=_as_probability(
            "feedback.reward_weight",
            feedback.get("reward_weight"),
            default=0.15,
        ),
        penalty_weight=_as_probability(
            "feedback.penalty_weight",
            feedback.get("penalty_weight"),
            default=0.2,
        ),
        suppress_threshold=_as_non_negative_int(
            "feedback.suppress_threshold",
            feedback.get("suppress_threshold"),
            default=3,
        ),
        suppress_penalty=_as_probability(
            "feedback.suppress_penalty",
            feedback.get("suppress_penalty"),
            default=0.5,
        ),
    )


def _load_retrieval(root: Mapping[str, object]) -> RetrievalExtensionsConfig:
    """Load retrieval extension settings."""
    retrieval = _as_mapping("retrieval", root.get("retrieval") or {})
    return RetrievalExtensionsConfig(
        adaptive_pool=_as_bool(
            "retrieval.adaptive_pool",
            retrieval.get("adaptive_pool"),
            default=False,
        ),
        adaptive_pool_max_multiplier=_as_positive_int(
            "retrieval.adaptive_pool_max_multiplier",
            retrieval.get("adaptive_pool_max_multiplier"),
            default=4,
        ),
    )


def load_config(path: pathlib.Path) -> HypermemoryConfig:
    """Load and validate a hypermemory config file."""
    raw = load_yaml(path)
    root = _as_mapping("hypermemory config", raw)
    config_dir = path.resolve().parent

    storage = _as_mapping("storage", root.get("storage") or {})
    workspace = _as_mapping("workspace", root.get("workspace") or {})
    corpus = _as_mapping("corpus", root.get("corpus") or {})

    workspace_root = _resolve_path(
        config_dir, _as_string("workspace.root", workspace.get("root"), default=".")
    )
    db_path = pathlib.Path(
        _as_string("storage.db_path", storage.get("db_path"), default=DEFAULT_DB_PATH)
    )
    if not db_path.is_absolute():
        db_path = (workspace_root / db_path).resolve()
    memory_file_names = _as_string_list(
        "workspace.memory_file_names",
        workspace.get("memory_file_names"),
        default=DEFAULT_MEMORY_FILE_NAMES,
    )
    daily_dir = _as_string(
        "workspace.daily_dir", workspace.get("daily_dir"), default=DEFAULT_DAILY_DIR
    )
    bank_dir = _as_string("workspace.bank_dir", workspace.get("bank_dir"), default=DEFAULT_BANK_DIR)
    include_default_memory = _as_bool(
        "workspace.include_default_memory",
        workspace.get("include_default_memory"),
        default=True,
    )

    corpus_paths_raw = corpus.get("paths")
    corpus_paths: list[CorpusPathConfig] = []
    if corpus_paths_raw is not None:
        if not isinstance(corpus_paths_raw, list):
            raise TypeError("corpus.paths must be a list of mappings")
        for index, raw_entry in enumerate(corpus_paths_raw):
            entry = _as_mapping(f"corpus.paths[{index}]", raw_entry)
            name = _as_string(f"corpus.paths[{index}].name", entry.get("name"))
            path_value = _as_string(f"corpus.paths[{index}].path", entry.get("path"))
            pattern = _as_string(
                f"corpus.paths[{index}].pattern",
                entry.get("pattern"),
                default="**/*.md",
            )
            required = _as_bool(
                f"corpus.paths[{index}].required",
                entry.get("required"),
                default=False,
            )
            resolved_path = _resolve_path(config_dir, path_value)
            resolve_under_workspace(workspace_root, resolved_path)
            corpus_paths.append(
                CorpusPathConfig(
                    name=name,
                    path=resolved_path,
                    pattern=pattern,
                    required=required,
                )
            )

    return HypermemoryConfig(
        config_path=path.resolve(),
        workspace_root=workspace_root,
        db_path=db_path,
        memory_file_names=memory_file_names,
        daily_dir=daily_dir,
        bank_dir=bank_dir,
        include_default_memory=include_default_memory,
        corpus_paths=tuple(corpus_paths),
        max_snippet_chars=_as_positive_int(
            "limits.max_snippet_chars",
            _as_mapping("limits", root.get("limits") or {}).get("max_snippet_chars"),
            default=DEFAULT_SNIPPET_CHARS,
        ),
        default_max_results=_as_positive_int(
            "limits.default_max_results",
            _as_mapping("limits", root.get("limits") or {}).get("default_max_results"),
            default=DEFAULT_SEARCH_RESULTS,
        ),
        governance=_load_governance(root),
        ranking=_load_ranking(root),
        backend=_load_backend(root),
        embedding=_load_embedding(root),
        rerank=_load_rerank(root),
        hybrid=_load_hybrid(root),
        qdrant=_load_qdrant(root),
        injection=_load_injection(root),
        dedup=_load_dedup(root),
        capture=_load_capture(root),
        decay=_load_decay(root),
        noise=_load_noise(root),
        admission=_load_admission(root),
        fact_registry=_load_fact_registry(root),
        feedback=_load_feedback(root),
        retrieval=_load_retrieval(root),
    )
