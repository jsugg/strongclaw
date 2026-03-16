"""Configuration loading for strongclaw memory v2."""

from __future__ import annotations

import fnmatch
import pathlib
from collections.abc import Mapping, Sequence

from clawops.common import load_yaml
from clawops.memory_v2.governance import validate_scope
from clawops.memory_v2.models import (
    DEFAULT_AUTO_APPLY_SCOPE_PATTERNS,
    DEFAULT_BANK_DIR,
    DEFAULT_DAILY_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_DEFAULT_SCOPE,
    DEFAULT_MEMORY_FILE_NAMES,
    DEFAULT_READABLE_SCOPE_PATTERNS,
    DEFAULT_SEARCH_RESULTS,
    DEFAULT_SNIPPET_CHARS,
    DEFAULT_WRITABLE_SCOPE_PATTERNS,
    CorpusPathConfig,
    GovernanceConfig,
    MemoryV2Config,
    RankingConfig,
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
    )
