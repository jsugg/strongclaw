"""Packaged default values for StrongClaw hypermemory."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from typing import Final, Literal, cast

import yaml

DefaultsMapping = Mapping[str, object]
EntryTypeLiteral = Literal[
    "fact",
    "reflection",
    "opinion",
    "entity",
    "proposal",
    "paragraph",
    "section",
]

_DEFAULTS_RESOURCE: Final[str] = "resources/defaults.yaml"


def _load_defaults_document() -> DefaultsMapping:
    """Load the packaged defaults YAML."""
    resource = files("clawops.hypermemory").joinpath(_DEFAULTS_RESOURCE)
    payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("hypermemory defaults resource must deserialize to a mapping")
    return cast(DefaultsMapping, payload)


@lru_cache(maxsize=1)
def defaults_document() -> DefaultsMapping:
    """Return the cached packaged defaults mapping."""
    return _load_defaults_document()


def _section(root: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = root.get(name)
    if not isinstance(value, Mapping):
        raise TypeError(f"hypermemory defaults section {name!r} must be a mapping")
    return cast(Mapping[str, object], value)


def _string(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"hypermemory defaults {path} must be a string")
    return value


def _optional_string(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path=path)


def _string_list(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"hypermemory defaults {path} must be a sequence of strings")
    items = tuple(_string(item, path=path) for item in value)
    return items


def _bool(value: object, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"hypermemory defaults {path} must be a boolean")
    return value


def _int(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"hypermemory defaults {path} must be an integer")
    return value


def _float(value: object, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"hypermemory defaults {path} must be numeric")
    return float(value)


def _mapping(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"hypermemory defaults {path} must be a mapping")
    return cast(Mapping[str, object], value)


_DEFAULTS = defaults_document()
_STORAGE = _section(_DEFAULTS, "storage")
_WORKSPACE = _section(_DEFAULTS, "workspace")
_LIMITS = _section(_DEFAULTS, "limits")
_GOVERNANCE = _section(_DEFAULTS, "governance")
_BACKEND = _section(_DEFAULTS, "backend")
_EMBEDDING = _section(_DEFAULTS, "embedding")
_RERANK = _section(_DEFAULTS, "rerank")
_RERANK_LOCAL = _section(_RERANK, "local")
_RERANK_HTTP = _section(_RERANK, "compatible_http")
_HYBRID = _section(_DEFAULTS, "hybrid")
_QDRANT = _section(_DEFAULTS, "qdrant")
_INJECTION = _section(_DEFAULTS, "injection")
_DEDUP = _section(_DEFAULTS, "dedup")
_CAPTURE = _section(_DEFAULTS, "capture")
_CAPTURE_LLM = _section(_CAPTURE, "llm")
_DECAY = _section(_DEFAULTS, "decay")
_NOISE = _section(_DEFAULTS, "noise")
_ADMISSION = _section(_DEFAULTS, "admission")
_ADMISSION_PRIORS = _section(_ADMISSION, "type_priors")
_FACT_REGISTRY = _section(_DEFAULTS, "fact_registry")
_FEEDBACK = _section(_DEFAULTS, "feedback")
_RETRIEVAL = _section(_DEFAULTS, "retrieval")
_RETRIEVAL_TYPE_WEIGHTS = _section(_RETRIEVAL, "type_weights")
_RANKING = _section(_DEFAULTS, "ranking")
_ENGINE = _section(_DEFAULTS, "engine")
_BANK_HEADERS = _section(_ENGINE, "bank_headers")
_MEMORY_PRO = _section(_ENGINE, "memory_pro")
_MEMORY_PRO_CATEGORY = _section(_MEMORY_PRO, "category_map")
_MEMORY_PRO_IMPORTANCE = _section(_MEMORY_PRO, "importance_map")

DEFAULT_DB_PATH = _string(_STORAGE["db_path"], path="storage.db_path")
DEFAULT_MEMORY_FILE_NAMES = _string_list(
    _WORKSPACE["memory_file_names"], path="workspace.memory_file_names"
)
DEFAULT_DAILY_DIR = _string(_WORKSPACE["daily_dir"], path="workspace.daily_dir")
DEFAULT_BANK_DIR = _string(_WORKSPACE["bank_dir"], path="workspace.bank_dir")
DEFAULT_INCLUDE_DEFAULT_MEMORY = _bool(
    _WORKSPACE["include_default_memory"], path="workspace.include_default_memory"
)
DEFAULT_SNIPPET_CHARS = _int(_LIMITS["max_snippet_chars"], path="limits.max_snippet_chars")
DEFAULT_SEARCH_RESULTS = _int(_LIMITS["default_max_results"], path="limits.default_max_results")
DEFAULT_DEFAULT_SCOPE = _string(_GOVERNANCE["default_scope"], path="governance.default_scope")
DEFAULT_READABLE_SCOPE_PATTERNS = _string_list(
    _GOVERNANCE["readable_scopes"], path="governance.readable_scopes"
)
DEFAULT_WRITABLE_SCOPE_PATTERNS = _string_list(
    _GOVERNANCE["writable_scopes"], path="governance.writable_scopes"
)
DEFAULT_AUTO_APPLY_SCOPE_PATTERNS = _string_list(
    _GOVERNANCE["auto_apply_scopes"], path="governance.auto_apply_scopes"
)
DEFAULT_SEARCH_BACKEND = _string(_BACKEND["active"], path="backend.active")
DEFAULT_FALLBACK_BACKEND = _string(_BACKEND["fallback"], path="backend.fallback")
DEFAULT_HYPERMEMORY_SEARCH_BACKEND = _string(
    _BACKEND["default_hypermemory_backend"],
    path="backend.default_hypermemory_backend",
)
DEFAULT_EMBEDDING_PROVIDER = _string(_EMBEDDING["provider"], path="embedding.provider")
DEFAULT_RERANK_PROVIDER = _string(_RERANK["provider"], path="rerank.provider")
DEFAULT_QDRANT_URL = _string(_QDRANT["url"], path="qdrant.url")
DEFAULT_QDRANT_COLLECTION = _string(_QDRANT["collection"], path="qdrant.collection")
DEFAULT_QDRANT_DENSE_VECTOR_NAME = _string(
    _QDRANT["dense_vector_name"], path="qdrant.dense_vector_name"
)
DEFAULT_QDRANT_SPARSE_VECTOR_NAME = _string(
    _QDRANT["sparse_vector_name"], path="qdrant.sparse_vector_name"
)

BANK_HEADERS = {
    key: _string(value, path=f"engine.bank_headers.{key}") + "\n"
    for key, value in _BANK_HEADERS.items()
}
WRITABLE_PREFIXES = _string_list(_ENGINE["writable_prefixes"], path="engine.writable_prefixes")
MEMORY_PRO_CATEGORY_MAP = {
    key: _string(value, path=f"engine.memory_pro.category_map.{key}")
    for key, value in _MEMORY_PRO_CATEGORY.items()
}
MEMORY_PRO_IMPORTANCE_MAP = {
    key: _float(value, path=f"engine.memory_pro.importance_map.{key}")
    for key, value in _MEMORY_PRO_IMPORTANCE.items()
}
SEARCH_TYPE_WEIGHTS = {
    cast(EntryTypeLiteral, key): _float(value, path=f"retrieval.type_weights.{key}")
    for key, value in _RETRIEVAL_TYPE_WEIGHTS.items()
}


def _compiled_pattern_rules(
    values: object,
    *,
    path: str,
    nullable_value: bool,
) -> tuple[tuple[re.Pattern[str], str | None], ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError(f"hypermemory defaults {path} must be a sequence")
    compiled: list[tuple[re.Pattern[str], str | None]] = []
    for index, value in enumerate(values):
        mapping = _mapping(value, path=f"{path}[{index}]")
        pattern = re.compile(_string(mapping.get("pattern"), path=f"{path}[{index}].pattern"))
        key_value = mapping.get("key")
        if nullable_value:
            key = _optional_string(key_value, path=f"{path}[{index}].key")
        else:
            key = _string(key_value, path=f"{path}[{index}].key")
        compiled.append((pattern, key))
    return tuple(compiled)


FACT_KEY_INFERENCE_RULES = _compiled_pattern_rules(
    _ENGINE["fact_key_inference_rules"],
    path="engine.fact_key_inference_rules",
    nullable_value=True,
)
FACT_QUERY_RULES = cast(
    tuple[tuple[re.Pattern[str], str], ...],
    _compiled_pattern_rules(
        _ENGINE["fact_query_rules"],
        path="engine.fact_query_rules",
        nullable_value=False,
    ),
)


RANKING_DEFAULTS = _RANKING
DEDUP_DEFAULTS = _DEDUP
CAPTURE_DEFAULTS = _CAPTURE
CAPTURE_LLM_DEFAULTS = _CAPTURE_LLM
DECAY_DEFAULTS = _DECAY
NOISE_DEFAULTS = _NOISE
ADMISSION_DEFAULTS = _ADMISSION
ADMISSION_TYPE_PRIORS_DEFAULTS = {
    key: _float(value, path=f"admission.type_priors.{key}")
    for key, value in _ADMISSION_PRIORS.items()
}
BACKEND_DEFAULTS = _BACKEND
EMBEDDING_DEFAULTS = _EMBEDDING
RERANK_DEFAULTS = _RERANK
RERANK_LOCAL_DEFAULTS = _RERANK_LOCAL
RERANK_HTTP_DEFAULTS = _RERANK_HTTP
HYBRID_DEFAULTS = _HYBRID
QDRANT_DEFAULTS = _QDRANT
INJECTION_DEFAULTS = _INJECTION
FACT_REGISTRY_DEFAULTS = _FACT_REGISTRY
FEEDBACK_DEFAULTS = _FEEDBACK
RETRIEVAL_DEFAULTS = _RETRIEVAL

SearchBackendLiteral = Literal["sqlite_fts", "qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"]
EmbeddingProviderLiteral = Literal["disabled", "compatible-http"]
RerankProviderLiteral = Literal["none", "local-sentence-transformers", "compatible-http"]

DEFAULT_SEARCH_BACKEND_LITERAL = cast(SearchBackendLiteral, DEFAULT_SEARCH_BACKEND)
DEFAULT_FALLBACK_BACKEND_LITERAL = cast(SearchBackendLiteral, DEFAULT_FALLBACK_BACKEND)
DEFAULT_HYPERMEMORY_SEARCH_BACKEND_LITERAL = cast(
    SearchBackendLiteral, DEFAULT_HYPERMEMORY_SEARCH_BACKEND
)
DEFAULT_EMBEDDING_PROVIDER_LITERAL = cast(EmbeddingProviderLiteral, DEFAULT_EMBEDDING_PROVIDER)
DEFAULT_RERANK_PROVIDER_LITERAL = cast(RerankProviderLiteral, DEFAULT_RERANK_PROVIDER)
