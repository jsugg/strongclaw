"""Configuration loading for strongclaw memory v2."""

from __future__ import annotations

import fnmatch
import pathlib
from collections.abc import Mapping, Sequence
from typing import cast

from clawops.common import load_yaml
from clawops.memory_v2.governance import validate_scope
from clawops.memory_v2.models import (
    DEFAULT_AUTO_APPLY_SCOPE_PATTERNS,
    DEFAULT_BANK_DIR,
    DEFAULT_DAILY_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_DEFAULT_SCOPE,
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_FALLBACK_BACKEND,
    DEFAULT_MEMORY_FILE_NAMES,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    DEFAULT_READABLE_SCOPE_PATTERNS,
    DEFAULT_RERANK_PROVIDER,
    DEFAULT_SEARCH_BACKEND,
    DEFAULT_SEARCH_RESULTS,
    DEFAULT_SNIPPET_CHARS,
    DEFAULT_WRITABLE_SCOPE_PATTERNS,
    BackendConfig,
    CorpusPathConfig,
    EmbeddingConfig,
    EmbeddingProviderKind,
    FusionMode,
    GovernanceConfig,
    HybridConfig,
    InjectionConfig,
    MemoryV2Config,
    QdrantConfig,
    RankingConfig,
    RerankConfig,
    RerankProviderKind,
    SearchBackend,
)


def _as_mapping(name: str, value: object) -> Mapping[str, object]:
    """Validate a mapping-shaped configuration section."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _as_string(name: str, value: object, *, default: str | None = None) -> str:
    """Validate a string configuration value."""
    if value is None:
        if default is None:
            raise TypeError(f"{name} must be a string")
        return default
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value.strip()


def _as_bool(name: str, value: object, *, default: bool) -> bool:
    """Validate a boolean configuration value."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _as_optional_string(name: str, value: object) -> str | None:
    """Validate an optional string configuration value."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string when provided")
    return value.strip()


def _as_blankable_string(name: str, value: object, *, default: str = "") -> str:
    """Validate a string configuration value that may be blank."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value.strip()


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
    """Return the shipped default memory-v2 config path."""
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    return repo_root / "platform/configs/memory/memory-v2.yaml"


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
    )


def _as_search_backend(name: str, value: object, *, default: SearchBackend) -> SearchBackend:
    """Validate a configured search backend."""
    backend = _as_string(name, value, default=default)
    if backend not in {"sqlite_fts", "qdrant_dense_hybrid"}:
        raise ValueError(f"{name} must be sqlite_fts or qdrant_dense_hybrid")
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


def _load_rerank(root: Mapping[str, object]) -> RerankConfig:
    """Load reranking configuration."""
    rerank = _as_mapping("rerank", root.get("rerank") or {})
    provider = _as_string(
        "rerank.provider", rerank.get("provider"), default=DEFAULT_RERANK_PROVIDER
    )
    if provider != "none":
        raise ValueError("rerank.provider currently supports only none")
    return RerankConfig(
        enabled=_as_bool("rerank.enabled", rerank.get("enabled"), default=False),
        provider=cast(RerankProviderKind, provider),
        model=_as_blankable_string("rerank.model", rerank.get("model")),
        base_url=_as_blankable_string("rerank.base_url", rerank.get("base_url")),
        api_key_env=_as_optional_string("rerank.api_key_env", rerank.get("api_key_env")),
        api_key=_as_optional_string("rerank.api_key", rerank.get("api_key")),
        timeout_ms=_as_positive_int("rerank.timeout_ms", rerank.get("timeout_ms"), default=15_000),
        top_k=_as_non_negative_int("rerank.top_k", rerank.get("top_k"), default=0),
    )


def _load_hybrid(root: Mapping[str, object]) -> HybridConfig:
    """Load hybrid retrieval configuration."""
    hybrid = _as_mapping("hybrid", root.get("hybrid") or {})
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
        rerank_top_k=_as_non_negative_int(
            "hybrid.rerank_top_k", hybrid.get("rerank_top_k"), default=0
        ),
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


def load_config(path: pathlib.Path) -> MemoryV2Config:
    """Load and validate a memory-v2 config file."""
    raw = load_yaml(path)
    root = _as_mapping("memory-v2 config", raw)
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
            resolved_path = _resolve_path(config_dir, path_value)
            resolve_under_workspace(workspace_root, resolved_path)
            corpus_paths.append(CorpusPathConfig(name=name, path=resolved_path, pattern=pattern))

    return MemoryV2Config(
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
    )
