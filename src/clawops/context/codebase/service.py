"""Scale-aware codebase context provider."""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal, Protocol, cast

import requests

from clawops.common import canonical_json, expand, load_yaml, sha256_hex, utc_now_ms, write_text
from clawops.context.contracts import ContextScale, validate_context_scale
from clawops.hypermemory.contracts import SparseVectorPayload, VectorPoint
from clawops.hypermemory.models import (
    DenseSearchCandidate,
    EmbeddingConfig,
    EmbeddingProviderKind,
    FusionMode,
    HybridConfig,
    QdrantConfig,
    RerankConfig,
    RerankProviderKind,
    SparseSearchCandidate,
)
from clawops.hypermemory.providers import (
    EmbeddingProvider,
    RerankProvider,
    create_embedding_provider,
    create_rerank_provider,
)
from clawops.hypermemory.qdrant_backend import QdrantBackend, VectorBackend
from clawops.hypermemory.sparse import SparseEncoder, build_sparse_encoder
from clawops.observability import emit_structured_log, observed_span

DEFAULT_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
}
CHUNKER_VERSION = 1
DEFAULT_CODEBASE_QDRANT_COLLECTION = "strongclaw-codebase-context"

SYMBOL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r"^\s*export\s+function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\s*", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\s*", re.MULTILINE),
    ],
    "go": [
        re.compile(r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+struct\b", re.MULTILINE),
    ],
}
IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*import\s+([A-Za-z0-9_., ]+)", re.MULTILINE),
        re.compile(r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\s+", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r'^\s*import\s+.*?\s+from\s+["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^\s*export\s+.*?\s+from\s+["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^\s*import\s+["\']([^"\']+)["\']', re.MULTILINE),
    ],
    "javascript": [
        re.compile(r'^\s*import\s+.*?\s+from\s+["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^\s*export\s+.*?\s+from\s+["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^\s*require\(["\']([^"\']+)["\']\)', re.MULTILINE),
    ],
}

type SymlinkPolicy = Literal["follow", "in_repo_only", "never"]
type GraphBackendName = Literal["sqlite", "neo4j"]


def _matches_path_pattern(path_text: str, pattern: str) -> bool:
    """Return True when a repo-relative path matches a configured glob."""
    return fnmatch.fnmatch(path_text, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(path_text, pattern.removeprefix("**/"))
    )


def detect_language(path: pathlib.Path) -> str:
    """Map a path to a coarse language name."""
    ext = path.suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
    }.get(ext, "text")


def extract_symbols(path: pathlib.Path, text: str) -> list[str]:
    """Extract a small symbol list with regex heuristics."""
    language = detect_language(path)
    patterns = SYMBOL_PATTERNS.get(language, [])
    symbols: list[str] = []
    for pattern in patterns:
        symbols.extend(pattern.findall(text))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in symbols:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:64]


def _line_number_for_offset(text: str, offset: int) -> int:
    """Return the 1-based line number for *offset* inside *text*."""
    return text.count("\n", 0, offset) + 1


def _split_text_block(
    *,
    path: str,
    language: str,
    symbol: str | None,
    kind: str,
    start_line: int,
    lines: Sequence[str],
) -> list["ChunkRecord"]:
    """Split one logical block into bounded chunk records."""
    chunk_records: list[ChunkRecord] = []
    line_cursor = start_line
    block_lines = list(lines)
    if not block_lines:
        block_lines = [""]
    for offset in range(0, len(block_lines), 80):
        window = block_lines[offset : offset + 80]
        window_text = "\n".join(window).rstrip()
        if not window_text:
            continue
        window_start = line_cursor + offset
        window_end = window_start + len(window) - 1
        content_hash = sha256_hex(window_text)
        chunk_id = sha256_hex(
            canonical_json(
                {
                    "path": path,
                    "kind": kind,
                    "symbol": symbol,
                    "start_line": window_start,
                    "end_line": window_end,
                    "content_hash": content_hash,
                    "chunker_version": CHUNKER_VERSION,
                }
            )
        )
        chunk_records.append(
            ChunkRecord(
                chunk_id=chunk_id,
                path=path,
                language=language,
                kind=kind,
                start_line=window_start,
                end_line=window_end,
                symbol=symbol,
                scope_chain=None if symbol is None else symbol,
                content=window_text,
                content_hash=content_hash,
            )
        )
    return chunk_records


def build_chunks(path: str, text: str, *, language: str) -> list["ChunkRecord"]:
    """Build stable chunk records for one indexed file."""
    lines = text.splitlines()
    if not lines:
        return _split_text_block(
            path=path,
            language=language,
            symbol=None,
            kind="module",
            start_line=1,
            lines=("",),
        )

    patterns = SYMBOL_PATTERNS.get(language, [])
    symbol_matches: list[tuple[int, str, str]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            line_number = _line_number_for_offset(text, match.start())
            matched_text = match.group(0)
            kind = "class" if "class" in matched_text else "function"
            symbol_matches.append((line_number, match.group(1), kind))
    symbol_matches.sort(key=lambda item: item[0])

    if not symbol_matches:
        chunk_records: list[ChunkRecord] = []
        block_start = 1
        block_lines: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            if line.strip():
                block_lines.append(line)
                continue
            if block_lines:
                chunk_records.extend(
                    _split_text_block(
                        path=path,
                        language=language,
                        symbol=None,
                        kind="text" if language == "text" else "module",
                        start_line=block_start,
                        lines=tuple(block_lines),
                    )
                )
                block_lines = []
            block_start = line_number + 1
        if block_lines:
            chunk_records.extend(
                _split_text_block(
                    path=path,
                    language=language,
                    symbol=None,
                    kind="text" if language == "text" else "module",
                    start_line=block_start,
                    lines=tuple(block_lines),
                )
            )
        return chunk_records

    chunk_records = []
    for index, (start_line, symbol, kind) in enumerate(symbol_matches):
        next_start = (
            symbol_matches[index + 1][0] if index + 1 < len(symbol_matches) else len(lines) + 1
        )
        window = lines[start_line - 1 : next_start - 1]
        chunk_records.extend(
            _split_text_block(
                path=path,
                language=language,
                symbol=symbol,
                kind=kind,
                start_line=start_line,
                lines=tuple(window),
            )
        )
    return chunk_records


def _python_import_candidates(import_target: str) -> tuple[str, ...]:
    """Return repo-relative Python module path candidates."""
    normalized = import_target.strip().replace(".", "/")
    if not normalized:
        return ()
    return (
        f"{normalized}.py",
        f"{normalized}/__init__.py",
    )


def _module_stem(path: str) -> tuple[str, ...]:
    """Return normalized path stems without extensions."""
    base = path.rsplit(".", 1)[0]
    index_base = base.removesuffix("/__init__")
    return tuple(candidate for candidate in {base, index_base} if candidate)


def _relative_import_candidates(path: str, import_target: str) -> tuple[str, ...]:
    """Return repo-relative import path candidates for JS/TS relative imports."""
    parent = pathlib.PurePosixPath(path).parent
    resolved = (parent / import_target).as_posix()
    stems = (
        resolved,
        resolved.removesuffix(".ts"),
        resolved.removesuffix(".tsx"),
        resolved.removesuffix(".js"),
        resolved.removesuffix(".jsx"),
    )
    candidates: list[str] = []
    for stem in stems:
        candidates.extend(
            [
                f"{stem}.ts",
                f"{stem}.tsx",
                f"{stem}.js",
                f"{stem}.jsx",
                f"{stem}/index.ts",
                f"{stem}/index.tsx",
                f"{stem}/index.js",
                f"{stem}/index.jsx",
            ]
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return tuple(deduped)


def resolve_import_paths(
    path: str, *, language: str, import_target: str, all_paths: set[str]
) -> tuple[str, ...]:
    """Resolve one import target to repo-relative file paths."""
    if language == "python":
        return tuple(
            candidate
            for candidate in _python_import_candidates(import_target)
            if candidate in all_paths
        )
    if language in {"typescript", "javascript"} and import_target.startswith("."):
        return tuple(
            candidate
            for candidate in _relative_import_candidates(path, import_target)
            if candidate in all_paths
        )
    if language in {"typescript", "javascript"}:
        target_stem = import_target.strip().lstrip("@").replace("/", "/")
        return tuple(rel_path for rel_path in all_paths if target_stem in _module_stem(rel_path))
    return ()


def extract_import_edges(
    path: str, text: str, *, language: str, all_paths: set[str]
) -> list["EdgeRecord"]:
    """Extract simple import edges for one file."""
    patterns = IMPORT_PATTERNS.get(language, [])
    edges: list[EdgeRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw_target = match.group(1).strip()
            if language == "python" and "," in raw_target:
                targets = [item.strip().split(" ", 1)[0] for item in raw_target.split(",")]
            else:
                targets = [raw_target]
            for target in targets:
                for resolved in resolve_import_paths(
                    path, language=language, import_target=target, all_paths=all_paths
                ):
                    edge_key = (path, resolved, "IMPORTS")
                    if edge_key in seen:
                        continue
                    seen.add(edge_key)
                    edges.append(
                        EdgeRecord(
                            src_id=f"path:{path}",
                            dst_id=f"path:{resolved}",
                            edge_type="IMPORTS",
                            path=path,
                            weight=1,
                        )
                    )
    return edges


def _as_mapping(name: str, value: object) -> dict[str, object]:
    """Validate a mapping-shaped config section."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{name} config must be a mapping")
    return cast(dict[str, object], value)


def _as_string(name: str, value: object, *, default: str) -> str:
    """Validate a string config value."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _resolve_env_reference(value: object) -> object:
    """Resolve `os.environ/KEY` references inside string config values."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith("os.environ/"):
        return stripped
    env_key = stripped.removeprefix("os.environ/").strip()
    if not env_key:
        raise ValueError("environment-backed config reference must name a variable")
    return os.environ.get(env_key, "").strip()


def _as_env_string(name: str, value: object, *, default: str) -> str:
    """Validate a string config value and expand environment references."""
    resolved = _resolve_env_reference(value)
    if resolved in {None, ""}:
        return default
    if not isinstance(resolved, str):
        raise TypeError(f"{name} must be a string")
    return resolved


def _as_optional_env_string(name: str, value: object) -> str | None:
    """Validate an optional string config value and expand environment references."""
    resolved = _resolve_env_reference(value)
    if resolved in {None, ""}:
        return None
    if not isinstance(resolved, str):
        raise TypeError(f"{name} must be a string")
    return resolved


def _as_string_list(name: str, value: object) -> list[str]:
    """Validate a list of string config values."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list of strings")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise TypeError(f"{name} must be a list of strings")
    return [cast(str, item) for item in items]


def _as_bool(name: str, value: object, *, default: bool) -> bool:
    """Validate a boolean config value."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _as_positive_int(name: str, value: object, *, default: int) -> int:
    """Validate a positive integer config value."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _as_optional_positive_int(name: str, value: object) -> int | None:
    """Validate an optional positive integer config value."""
    if value in {None, ""}:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _as_float(name: str, value: object, *, default: float) -> float:
    """Validate a floating-point config value."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    return float(value)


def _as_graph_backend(name: str, value: object, *, default: GraphBackendName) -> GraphBackendName:
    """Validate the configured graph backend."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in {"sqlite", "neo4j"}:
        raise ValueError(f"{name} must be one of: neo4j, sqlite")
    return cast(GraphBackendName, value)


def _as_symlink_policy(name: str, value: object, *, default: SymlinkPolicy) -> SymlinkPolicy:
    """Validate the symlink handling policy."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in {"follow", "in_repo_only", "never"}:
        raise ValueError(f"{name} must be one of: follow, in_repo_only, never")
    return cast(SymlinkPolicy, value)


def _as_embedding_provider(
    name: str, value: object, *, default: EmbeddingProviderKind
) -> EmbeddingProviderKind:
    """Validate the configured embedding provider kind."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in {"disabled", "compatible-http"}:
        raise ValueError(f"{name} must be one of: compatible-http, disabled")
    return cast(EmbeddingProviderKind, value)


def _as_rerank_provider(
    name: str, value: object, *, default: RerankProviderKind
) -> RerankProviderKind:
    """Validate the configured rerank provider kind."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in {"none", "local-sentence-transformers", "compatible-http"}:
        raise ValueError(
            f"{name} must be one of: compatible-http, local-sentence-transformers, none"
        )
    return cast(RerankProviderKind, value)


def _as_fusion_mode(name: str, value: object, *, default: FusionMode) -> FusionMode:
    """Validate the configured hybrid fusion mode."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in {"rrf", "weighted"}:
        raise ValueError(f"{name} must be one of: rrf, weighted")
    return cast(FusionMode, value)


def _chunk_item_id(chunk_id: str) -> int:
    """Return a deterministic positive integer ID for one chunk."""
    return int(chunk_id[:15], 16)


def _contextualized_chunk_text(
    *,
    path: str,
    language: str,
    kind: str,
    symbol: str | None,
    content: str,
) -> str:
    """Return the retrieval text used for vector and rerank lanes."""
    parts = [path, language, kind]
    if symbol:
        parts.append(symbol)
    parts.append(content)
    return "\n".join(part for part in parts if part).strip()


def _iter_batches[T](items: Sequence[T], batch_size: int) -> Iterable[Sequence[T]]:
    """Yield stable batches from *items*."""
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _rank_contribution(*, rank: int, weight: float, hybrid: HybridConfig) -> float:
    """Return one lane contribution for the fused hybrid score."""
    if hybrid.fusion == "rrf":
        return weight * (1.0 / float(hybrid.rrf_k + rank))
    return weight * (1.0 / float(rank))


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    language TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    symbols TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    path,
    content,
    symbols,
    content='',
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol TEXT,
    scope_chain TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id,
    path,
    content,
    symbol,
    scope_chain,
    content='',
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS edges (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    path TEXT,
    weight INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (src_id, dst_id, edge_type)
);

CREATE TABLE IF NOT EXISTS index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    item_id INTEGER NOT NULL UNIQUE,
    point_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    indexed_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_vectors_path ON chunk_vectors(path);

CREATE TABLE IF NOT EXISTS sparse_terms (
    term TEXT PRIMARY KEY,
    term_id INTEGER NOT NULL UNIQUE,
    document_freq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hybrid_pending_deletions (
    point_id TEXT PRIMARY KEY
);
"""

MIGRATION_COLUMNS = {
    "size_bytes": "INTEGER NOT NULL DEFAULT 0",
}


@dataclasses.dataclass(frozen=True, slots=True)
class IndexStats:
    """Stable indexing summary."""

    total_files: int
    indexed_files: int
    skipped_files: int
    deleted_files: int

    def to_dict(self) -> dict[str, int]:
        """Serialize the stats for CLI/reporting surfaces."""
        return {
            "total_files": self.total_files,
            "indexed_files": self.indexed_files,
            "skipped_files": self.skipped_files,
            "deleted_files": self.deleted_files,
        }


@dataclasses.dataclass(slots=True)
class SearchHit:
    """Search result."""

    path: str
    snippet: str
    symbols: list[str]
    start_line: int
    end_line: int
    kind: str = "file"
    chunk_id: str | None = None


@dataclasses.dataclass(slots=True)
class HybridRankedChunk:
    """One merged hybrid candidate with its backing row."""

    row: sqlite3.Row
    score: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class IndexedFile:
    """Indexed file metadata used by context envelopes."""

    path: str
    sha256: str
    size_bytes: int
    symbols: tuple[str, ...]
    content: str


@dataclasses.dataclass(frozen=True, slots=True)
class ChunkRecord:
    """Chunk metadata stored for medium and large scales."""

    chunk_id: str
    path: str
    language: str
    kind: str
    start_line: int
    end_line: int
    symbol: str | None
    scope_chain: str | None
    content: str
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class EdgeRecord:
    """Graph edge metadata stored in SQLite and optional Neo4j."""

    src_id: str
    dst_id: str
    edge_type: str
    path: str | None
    weight: int = 1


@dataclasses.dataclass(frozen=True, slots=True)
class GraphNode:
    """Graph node payload for optional external graph backends."""

    node_id: str
    path: str
    language: str
    kind: str


@dataclasses.dataclass(frozen=True, slots=True)
class GraphConfig:
    """Graph expansion configuration."""

    enabled: bool = True
    backend: GraphBackendName = "neo4j"
    allow_degraded_fallback: bool = True
    neo4j_url: str = "http://127.0.0.1:7474"
    neo4j_username_env: str = "NEO4J_USERNAME"
    neo4j_password_env: str = "NEO4J_PASSWORD"
    database: str = "neo4j"
    depth: int = 1
    limit: int = 12


@dataclasses.dataclass(frozen=True, slots=True)
class CodebaseContextConfig:
    """Codebase context provider configuration."""

    db_path: pathlib.Path
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_file_size_bytes: int = 1_000_000
    include_hidden: bool = False
    symlink_policy: SymlinkPolicy = "in_repo_only"
    graph: GraphConfig = dataclasses.field(default_factory=GraphConfig)
    embedding: EmbeddingConfig = dataclasses.field(
        default_factory=lambda: EmbeddingConfig(enabled=False, provider="disabled")
    )
    rerank: RerankConfig = dataclasses.field(
        default_factory=lambda: RerankConfig(
            enabled=False,
            provider="none",
            fallback_provider="none",
        )
    )
    hybrid: HybridConfig = dataclasses.field(default_factory=HybridConfig)
    qdrant: QdrantConfig = dataclasses.field(
        default_factory=lambda: QdrantConfig(
            enabled=False,
            collection=DEFAULT_CODEBASE_QDRANT_COLLECTION,
        )
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CodebaseContextFeatures:
    """Resolved runtime features for one scale invocation."""

    scale: ContextScale
    backend_modes: tuple[str, ...]
    graph_backend: GraphBackendName
    hybrid_backend: Literal["none", "qdrant"] = "none"
    degraded_graph: bool = False
    degraded_hybrid: bool = False


class GraphBackend(Protocol):
    """Graph backend contract."""

    enabled: bool

    def health(self) -> dict[str, object]:
        """Return a machine-readable health payload."""
        ...

    def upsert(
        self,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[EdgeRecord],
        snapshot_id: str,
    ) -> None:
        """Upsert graph nodes and edges."""
        ...

    def neighbors(
        self,
        *,
        node_id: str,
        edge_types: Sequence[str],
        depth: int,
        limit: int,
    ) -> list[str]:
        """Return neighboring node IDs."""
        ...


class SqliteGraphBackend:
    """SQLite graph backend using the local derived edges table."""

    enabled = True

    def __init__(self, conn_factory: Callable[[], sqlite3.Connection]) -> None:
        self._conn_factory = conn_factory

    def health(self) -> dict[str, object]:
        """Return a machine-readable health payload."""
        return {"enabled": True, "healthy": True, "backend": "sqlite"}

    def upsert(
        self,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[EdgeRecord],
        snapshot_id: str,
    ) -> None:
        """Upsert graph data into SQLite."""
        del nodes, snapshot_id
        with self._conn_factory() as conn:
            conn.executemany(
                """
                INSERT INTO edges(src_id, dst_id, edge_type, path, weight)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(src_id, dst_id, edge_type) DO UPDATE SET
                    path = excluded.path,
                    weight = excluded.weight
                """,
                [
                    (edge.src_id, edge.dst_id, edge.edge_type, edge.path, edge.weight)
                    for edge in edges
                ],
            )
            conn.commit()

    def neighbors(
        self,
        *,
        node_id: str,
        edge_types: Sequence[str],
        depth: int,
        limit: int,
    ) -> list[str]:
        """Return neighboring node IDs using recursive SQL traversal."""
        if not edge_types:
            return []
        placeholders = ", ".join("?" for _ in edge_types)
        sql = f"""
        WITH RECURSIVE graph_walk(dst_id, remaining_depth) AS (
            SELECT dst_id, ? - 1
            FROM edges
            WHERE src_id = ?
              AND edge_type IN ({placeholders})
            UNION ALL
            SELECT edges.dst_id, graph_walk.remaining_depth - 1
            FROM edges
            JOIN graph_walk ON edges.src_id = graph_walk.dst_id
            WHERE graph_walk.remaining_depth > 0
              AND edges.edge_type IN ({placeholders})
        )
        SELECT DISTINCT dst_id
        FROM graph_walk
        WHERE dst_id != ?
        LIMIT ?
        """
        params: list[object] = [depth, node_id]
        params.extend(edge_types)
        params.extend(edge_types)
        params.extend([node_id, limit])
        with self._conn_factory() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [str(row["dst_id"]) for row in rows]


class Neo4jGraphBackend:
    """Neo4j graph backend using the transactional HTTP endpoint."""

    enabled = True

    def __init__(self, config: GraphConfig) -> None:
        self._config = config
        self._session = requests.Session()

    def _auth(self) -> tuple[str, str] | None:
        username = os.environ.get(self._config.neo4j_username_env)
        password = os.environ.get(self._config.neo4j_password_env)
        if username is None or password is None:
            return None
        return username, password

    def _endpoint(self) -> str:
        return f"{self._config.neo4j_url.rstrip('/')}/db/{self._config.database}/tx/commit"

    def _statement_result(
        self, statement: str, *, parameters: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        auth = self._auth()
        if auth is None:
            return {"enabled": True, "healthy": False, "backend": "neo4j", "reason": "missing auth"}
        response = self._session.post(
            self._endpoint(),
            json={"statements": [{"statement": statement, "parameters": dict(parameters or {})}]},
            auth=auth,
            headers={"Accept": "application/json;charset=UTF-8"},
            timeout=5.0,
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        errors = payload.get("errors", [])
        if isinstance(errors, list) and errors:
            message = cast(dict[str, object], errors[0]).get("message", "neo4j query failed")
            raise RuntimeError(str(message))
        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            return {}
        return cast(dict[str, object], results[0])

    def health(self) -> dict[str, object]:
        """Return a machine-readable health payload."""
        auth = self._auth()
        if auth is None:
            return {"enabled": True, "healthy": False, "backend": "neo4j", "reason": "missing auth"}
        try:
            self._statement_result("RETURN 1 AS ok")
        except (RuntimeError, requests.RequestException) as err:
            return {
                "enabled": True,
                "healthy": False,
                "backend": "neo4j",
                "reason": str(err),
            }
        return {"enabled": True, "healthy": True, "backend": "neo4j"}

    def _ensure_constraints(self) -> None:
        self._statement_result(
            "CREATE CONSTRAINT code_node_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE"
        )

    def upsert(
        self,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[EdgeRecord],
        snapshot_id: str,
    ) -> None:
        """Upsert graph nodes and edges into Neo4j."""
        if not nodes:
            return
        self._ensure_constraints()
        node_payload = [
            {
                "id": node.node_id,
                "path": node.path,
                "language": node.language,
                "kind": node.kind,
                "snapshot_id": snapshot_id,
            }
            for node in nodes
        ]
        edge_payload = [
            {
                "src_id": edge.src_id,
                "dst_id": edge.dst_id,
                "edge_type": edge.edge_type,
                "path": edge.path,
                "weight": edge.weight,
                "snapshot_id": snapshot_id,
            }
            for edge in edges
        ]
        self._statement_result(
            """
            UNWIND $nodes AS node
            MERGE (n:CodeNode {id: node.id})
            SET n.path = node.path,
                n.language = node.language,
                n.kind = node.kind,
                n.snapshot_id = node.snapshot_id
            """,
            parameters={"nodes": node_payload},
        )
        if edge_payload:
            self._statement_result(
                """
                UNWIND $edges AS edge
                MATCH (src:CodeNode {id: edge.src_id})
                MATCH (dst:CodeNode {id: edge.dst_id})
                MERGE (src)-[rel:IMPORTS {edge_type: edge.edge_type}]->(dst)
                SET rel.path = edge.path,
                    rel.weight = edge.weight,
                    rel.snapshot_id = edge.snapshot_id
                """,
                parameters={"edges": edge_payload},
            )
        self._statement_result(
            """
            MATCH (n:CodeNode)
            WHERE n.snapshot_id <> $snapshot_id
            DETACH DELETE n
            """,
            parameters={"snapshot_id": snapshot_id},
        )

    def neighbors(
        self,
        *,
        node_id: str,
        edge_types: Sequence[str],
        depth: int,
        limit: int,
    ) -> list[str]:
        """Return neighboring node IDs from Neo4j."""
        if not edge_types:
            return []
        result = self._statement_result(
            """
            MATCH (source:CodeNode {id: $node_id})-[rel*1..$depth]->(neighbor:CodeNode)
            WHERE ALL(item IN rel WHERE item.edge_type IN $edge_types)
            RETURN DISTINCT neighbor.id AS id
            LIMIT $limit
            """,
            parameters={
                "node_id": node_id,
                "edge_types": list(edge_types),
                "depth": depth,
                "limit": limit,
            },
        )
        data = result.get("data", [])
        if not isinstance(data, list):
            return []
        neighbors: list[str] = []
        for item_value in cast(list[object], data):
            if not isinstance(item_value, dict):
                continue
            row = cast(dict[str, object], item_value).get("row", [])
            if not isinstance(row, list) or not row:
                continue
            row_values = cast(list[object], row)
            row_value = row_values[0]
            neighbors.append("" if row_value is None else str(row_value))
        return neighbors


class CodebaseContextService:
    """Lexical, chunk-aware codebase context service."""

    def __init__(
        self, repo: pathlib.Path, config: CodebaseContextConfig, *, scale: ContextScale
    ) -> None:
        self.repo = repo.expanduser().resolve()
        self.config = config
        self.scale: ContextScale = scale
        self.db_path = config.db_path.expanduser().resolve()
        self._sqlite_graph_backend = SqliteGraphBackend(self.connect)
        self._neo4j_graph_backend = Neo4jGraphBackend(config.graph)
        self._embedding_provider: EmbeddingProvider = create_embedding_provider(config.embedding)
        self._rerank_provider: RerankProvider = create_rerank_provider(config.rerank)
        self._vector_backend: VectorBackend = QdrantBackend(config.qdrant)

    def override_runtime_deps(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        rerank_provider: RerankProvider | None = None,
        vector_backend: VectorBackend | None = None,
    ) -> None:
        """Override runtime dependencies for controlled tests and harnesses."""
        if embedding_provider is not None:
            self._embedding_provider = embedding_provider
        if rerank_provider is not None:
            self._rerank_provider = rerank_provider
        if vector_backend is not None:
            self._vector_backend = vector_backend

    def connect(self) -> sqlite3.Connection:
        """Open the SQLite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Apply additive schema migrations for existing databases."""
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(files)").fetchall()}
        for column, definition in MIGRATION_COLUMNS.items():
            if column in columns:
                continue
            conn.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")
        conn.commit()

    def _hybrid_requested(self) -> bool:
        """Return whether the current scale and config request hybrid retrieval."""
        return (
            self.scale != "small" and self.config.embedding.enabled and self.config.qdrant.enabled
        )

    def _hybrid_state_ready(self) -> bool:
        """Return whether local vector state matches the indexed chunk corpus."""
        with self.connect() as conn:
            chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            vector_count = int(conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0])
            sparse_term_count = int(conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0])
            pending_delete_count = int(
                conn.execute("SELECT COUNT(*) FROM hybrid_pending_deletions").fetchone()[0]
            )
        if chunk_count == 0:
            return False
        return vector_count == chunk_count and sparse_term_count > 0 and pending_delete_count == 0

    def _hybrid_features(self) -> tuple[bool, bool]:
        """Return whether hybrid retrieval is active and whether it is degraded."""
        if not self._hybrid_requested():
            return False, False
        if not self.config.embedding.model or not self.config.embedding.base_url:
            return False, True
        health = self._vector_backend.health()
        if not bool(health.get("healthy", False)):
            return False, True
        if not self._hybrid_state_ready():
            return False, True
        return True, False

    def _runtime_features(self) -> CodebaseContextFeatures:
        """Resolve the active runtime feature set."""
        modes: list[str] = ["lexical"]
        hybrid_enabled, degraded_hybrid = self._hybrid_features()
        if hybrid_enabled:
            modes.append("hybrid")

        if self.scale == "small" or not self.config.graph.enabled:
            return CodebaseContextFeatures(
                scale=self.scale,
                backend_modes=tuple(modes),
                graph_backend="sqlite",
                hybrid_backend="qdrant" if hybrid_enabled else "none",
                degraded_graph=False,
                degraded_hybrid=degraded_hybrid,
            )
        if self.config.graph.backend == "neo4j":
            health = self._neo4j_graph_backend.health()
            if bool(health.get("healthy", False)):
                modes.append("graph")
                return CodebaseContextFeatures(
                    scale=self.scale,
                    backend_modes=tuple(modes),
                    graph_backend="neo4j",
                    hybrid_backend="qdrant" if hybrid_enabled else "none",
                    degraded_graph=False,
                    degraded_hybrid=degraded_hybrid,
                )
            if self.scale == "large":
                raise RuntimeError("large codebase context requires a healthy neo4j graph backend")
            emit_structured_log(
                "clawops.context.codebase.graph.degraded",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "requested_backend": "neo4j",
                    "fallback_backend": "sqlite",
                    "reason": str(health.get("reason", "neo4j unavailable")),
                },
            )
            modes.append("graph")
            return CodebaseContextFeatures(
                scale=self.scale,
                backend_modes=tuple(modes),
                graph_backend="sqlite",
                hybrid_backend="qdrant" if hybrid_enabled else "none",
                degraded_graph=True,
                degraded_hybrid=degraded_hybrid,
            )
        modes.append("graph")
        return CodebaseContextFeatures(
            scale=self.scale,
            backend_modes=tuple(modes),
            graph_backend="sqlite",
            hybrid_backend="qdrant" if hybrid_enabled else "none",
            degraded_graph=False,
            degraded_hybrid=degraded_hybrid,
        )

    def backend_modes(self) -> tuple[str, ...]:
        """Return the active retrieval mode names."""
        return self._runtime_features().backend_modes

    def _active_graph_backend(self) -> GraphBackend:
        features = self._runtime_features()
        if features.graph_backend == "neo4j":
            return self._neo4j_graph_backend
        return self._sqlite_graph_backend

    def _allows_symlink(self, path: pathlib.Path) -> bool:
        """Return True when *path* satisfies the configured symlink policy."""
        if not path.is_symlink():
            return True
        if self.config.symlink_policy == "never":
            return False
        if self.config.symlink_policy == "follow":
            return True
        try:
            resolved_target = path.resolve(strict=True)
        except OSError:
            return False
        if not resolved_target.is_file():
            return False
        try:
            resolved_target.relative_to(self.repo)
        except ValueError:
            return False
        return True

    def _load_indexed_metadata(self, conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
        """Load stored file metadata used for incremental skip decisions."""
        rows = conn.execute("SELECT path, mtime_ns, size_bytes FROM files").fetchall()
        return {str(row["path"]): (int(row["mtime_ns"]), int(row["size_bytes"])) for row in rows}

    def _load_index_state(self, conn: sqlite3.Connection) -> dict[str, str]:
        """Load persisted index state."""
        rows = conn.execute("SELECT key, value FROM index_state").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def iter_files(self, include_hidden: bool = False) -> Iterable[pathlib.Path]:
        """Yield indexable files from the repository."""
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            if not self._allows_symlink(path):
                continue
            rel_path = path.relative_to(self.repo)
            rel_text = rel_path.as_posix()
            if not (include_hidden or self.config.include_hidden) and any(
                part.startswith(".") for part in rel_path.parts
            ):
                continue
            if path.suffix.lower() not in DEFAULT_TEXT_EXTENSIONS:
                continue
            if self.config.include_globs and not any(
                _matches_path_pattern(rel_text, pattern) for pattern in self.config.include_globs
            ):
                continue
            if self.config.exclude_globs and any(
                _matches_path_pattern(rel_text, pattern) for pattern in self.config.exclude_globs
            ):
                continue
            try:
                if path.stat().st_size > self.config.max_file_size_bytes:
                    continue
            except OSError:
                continue
            yield path

    def _store_changed_file(
        self,
        conn: sqlite3.Connection,
        *,
        rel_path: str,
        language: str,
        sha256: str,
        mtime_ns: int,
        size_bytes: int,
        symbols: Sequence[str],
        content: str,
        all_paths: set[str],
    ) -> list[str]:
        """Persist file, chunk, and edge state for one changed file."""
        symbols_text = "\n".join(symbols)
        stale_point_ids = [
            str(row["point_id"])
            for row in conn.execute(
                "SELECT point_id FROM chunk_vectors WHERE path = ? ORDER BY point_id ASC",
                (rel_path,),
            ).fetchall()
        ]
        conn.execute(
            """
            INSERT INTO files(path, sha256, language, mtime_ns, size_bytes, symbols, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                sha256 = excluded.sha256,
                language = excluded.language,
                mtime_ns = excluded.mtime_ns,
                size_bytes = excluded.size_bytes,
                symbols = excluded.symbols,
                content = excluded.content
            """,
            (rel_path, sha256, language, mtime_ns, size_bytes, symbols_text, content),
        )
        conn.execute("DELETE FROM files_fts WHERE path = ?", (rel_path,))
        conn.execute(
            "INSERT INTO files_fts(path, content, symbols) VALUES (?, ?, ?)",
            (rel_path, content, symbols_text),
        )
        conn.execute("DELETE FROM chunks WHERE path = ?", (rel_path,))
        conn.execute("DELETE FROM chunks_fts WHERE path = ?", (rel_path,))
        conn.execute("DELETE FROM chunk_vectors WHERE path = ?", (rel_path,))
        conn.execute("DELETE FROM edges WHERE path = ?", (rel_path,))

        chunk_records = build_chunks(rel_path, content, language=language)
        if chunk_records:
            now_ms = utc_now_ms()
            conn.executemany(
                """
                INSERT INTO chunks(
                    chunk_id,
                    path,
                    language,
                    kind,
                    start_line,
                    end_line,
                    symbol,
                    scope_chain,
                    content,
                    content_hash,
                    updated_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.path,
                        chunk.language,
                        chunk.kind,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.symbol,
                        chunk.scope_chain,
                        chunk.content,
                        chunk.content_hash,
                        now_ms,
                    )
                    for chunk in chunk_records
                ],
            )
            conn.executemany(
                """
                INSERT INTO chunks_fts(chunk_id, path, content, symbol, scope_chain)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.path,
                        chunk.content,
                        chunk.symbol or "",
                        chunk.scope_chain or "",
                    )
                    for chunk in chunk_records
                ],
            )

        edges = extract_import_edges(rel_path, content, language=language, all_paths=all_paths)
        if edges:
            conn.executemany(
                """
                INSERT INTO edges(src_id, dst_id, edge_type, path, weight)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(src_id, dst_id, edge_type) DO UPDATE SET
                    path = excluded.path,
                    weight = excluded.weight
                """,
                [
                    (edge.src_id, edge.dst_id, edge.edge_type, edge.path, edge.weight)
                    for edge in edges
                ],
            )
        return stale_point_ids

    def _clear_hybrid_state(self, conn: sqlite3.Connection) -> None:
        """Delete persisted hybrid state after a failed or disabled sync."""
        conn.execute("DELETE FROM chunk_vectors")
        conn.execute("DELETE FROM sparse_terms")
        conn.executemany(
            "DELETE FROM index_state WHERE key = ?",
            (("sparse_fingerprint",), ("sparse_doc_count",), ("sparse_avg_doc_length",)),
        )

    def _queue_hybrid_deletions(
        self,
        conn: sqlite3.Connection,
        *,
        point_ids: Sequence[str],
    ) -> None:
        """Persist remote point deletions until the worker can reconcile them."""
        pending_ids = sorted({point_id for point_id in point_ids if point_id})
        if not pending_ids:
            return
        conn.executemany(
            "INSERT OR IGNORE INTO hybrid_pending_deletions(point_id) VALUES (?)",
            ((point_id,) for point_id in pending_ids),
        )

    def _load_pending_hybrid_deletions(self, conn: sqlite3.Connection) -> list[str]:
        """Load queued remote point deletions in stable order."""
        rows = conn.execute(
            "SELECT point_id FROM hybrid_pending_deletions ORDER BY point_id ASC"
        ).fetchall()
        return [str(row["point_id"]) for row in rows]

    def _clear_pending_hybrid_deletions(
        self,
        conn: sqlite3.Connection,
        *,
        point_ids: Sequence[str],
    ) -> None:
        """Acknowledge one or more queued point deletions after a successful sync."""
        acknowledged_ids = sorted({point_id for point_id in point_ids if point_id})
        if not acknowledged_ids:
            return
        conn.executemany(
            "DELETE FROM hybrid_pending_deletions WHERE point_id = ?",
            ((point_id,) for point_id in acknowledged_ids),
        )

    def _persist_sparse_encoder(self, conn: sqlite3.Connection, encoder: SparseEncoder) -> None:
        """Persist the deterministic sparse vocabulary state."""
        conn.execute("DELETE FROM sparse_terms")
        conn.executemany(
            """
            INSERT INTO sparse_terms(term, term_id, document_freq)
            VALUES (?, ?, ?)
            """,
            [
                (term, term_id, int(encoder.document_frequency.get(term, 0)))
                for term, term_id in sorted(encoder.term_to_id.items(), key=lambda item: item[1])
            ],
        )
        conn.executemany(
            """
            INSERT INTO index_state(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [
                ("sparse_fingerprint", encoder.fingerprint),
                ("sparse_doc_count", str(encoder.document_count)),
                ("sparse_avg_doc_length", f"{encoder.average_document_length:.8f}"),
            ],
        )

    def _load_sparse_encoder(self, conn: sqlite3.Connection) -> SparseEncoder | None:
        """Load the persisted sparse encoder when available."""
        rows = conn.execute(
            "SELECT term, term_id, document_freq FROM sparse_terms ORDER BY term_id ASC"
        ).fetchall()
        if not rows:
            return None
        state = self._load_index_state(conn)
        document_count = int(state.get("sparse_doc_count", "0"))
        average_document_length = float(state.get("sparse_avg_doc_length", "0") or "0")
        fingerprint = state.get("sparse_fingerprint", "")
        if document_count <= 0 or not fingerprint:
            return None
        term_to_id = {str(row["term"]): int(row["term_id"]) for row in rows}
        document_frequency = {str(row["term"]): int(row["document_freq"]) for row in rows}
        return SparseEncoder(
            term_to_id=term_to_id,
            document_frequency=document_frequency,
            document_count=document_count,
            average_document_length=average_document_length,
            fingerprint=fingerprint,
        )

    def _sync_hybrid_index(self) -> None:
        """Synchronize chunk embeddings and sparse vectors into the Qdrant lane."""
        with self.connect() as conn:
            stale_point_ids = self._load_pending_hybrid_deletions(conn)
            if not self._hybrid_requested():
                self._clear_hybrid_state(conn)
                conn.commit()
                rows: list[sqlite3.Row] = []
            else:
                rows = conn.execute("""
                    SELECT chunk_id, path, language, kind, start_line, end_line, symbol, content, content_hash,
                           updated_at_ms
                    FROM chunks
                    ORDER BY path ASC, start_line ASC, chunk_id ASC
                    """).fetchall()
        if not self._hybrid_requested():
            try:
                if stale_point_ids:
                    self._vector_backend.delete_points(sorted(set(stale_point_ids)))
            except requests.RequestException:
                return
            if stale_point_ids:
                with self.connect() as conn:
                    self._clear_pending_hybrid_deletions(conn, point_ids=stale_point_ids)
                    conn.commit()
            return
        if not rows:
            with self.connect() as conn:
                self._clear_hybrid_state(conn)
                conn.commit()
            if stale_point_ids:
                try:
                    self._vector_backend.delete_points(sorted(set(stale_point_ids)))
                except requests.RequestException:
                    return
                with self.connect() as conn:
                    self._clear_pending_hybrid_deletions(conn, point_ids=stale_point_ids)
                    conn.commit()
            return
        try:
            health = self._vector_backend.health()
            if not bool(health.get("healthy", False)):
                raise RuntimeError(
                    str(health.get("error") or health.get("reason") or "qdrant unavailable")
                )
            if not self.config.embedding.model or not self.config.embedding.base_url:
                raise RuntimeError("embedding config is incomplete for hybrid retrieval")
            texts = [
                _contextualized_chunk_text(
                    path=str(row["path"]),
                    language=str(row["language"]),
                    kind=str(row["kind"]),
                    symbol=None if row["symbol"] is None else str(row["symbol"]),
                    content=str(row["content"]),
                )
                for row in rows
            ]
            sparse_encoder = build_sparse_encoder(texts)
            dense_vectors: list[list[float]] = []
            for batch in _iter_batches(texts, max(self.config.embedding.batch_size, 1)):
                dense_vectors.extend(self._embedding_provider.embed_texts(list(batch)))
            if len(dense_vectors) != len(rows):
                raise ValueError("embedded chunk count does not match the indexed chunk count")
            vector_size = len(dense_vectors[0])
            self._vector_backend.ensure_collection(vector_size=vector_size, include_sparse=True)
            points: list[VectorPoint] = []
            indexed_at_ms = utc_now_ms()
            for row, dense_vector, text in zip(rows, dense_vectors, texts, strict=True):
                chunk_id = str(row["chunk_id"])
                sparse_vector = sparse_encoder.encode_document(text)
                vector_payload: dict[str, list[float] | SparseVectorPayload] = {
                    self.config.qdrant.dense_vector_name: dense_vector
                }
                if not sparse_vector.is_empty:
                    vector_payload[self.config.qdrant.sparse_vector_name] = (
                        sparse_vector.to_qdrant()
                    )
                points.append(
                    VectorPoint(
                        id=chunk_id,
                        vector=vector_payload,
                        payload={
                            "item_id": _chunk_item_id(chunk_id),
                            "rel_path": str(row["path"]),
                            "lane": "corpus",
                            "source_name": "codebase",
                            "item_type": str(row["kind"]),
                            "scope": "global",
                            "start_line": int(row["start_line"]),
                            "end_line": int(row["end_line"]),
                            "modified_at": str(row["updated_at_ms"]),
                            "confidence": None,
                        },
                    )
                )
            self._vector_backend.upsert_points(points)
            stale_ids = sorted(set(stale_point_ids))
            if stale_ids:
                self._vector_backend.delete_points(stale_ids)
            with self.connect() as conn:
                self._persist_sparse_encoder(conn, sparse_encoder)
                conn.executemany(
                    """
                    INSERT INTO chunk_vectors(chunk_id, path, item_id, point_id, content_hash, indexed_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        path = excluded.path,
                        item_id = excluded.item_id,
                        point_id = excluded.point_id,
                        content_hash = excluded.content_hash,
                        indexed_at_ms = excluded.indexed_at_ms
                    """,
                    [
                        (
                            str(row["chunk_id"]),
                            str(row["path"]),
                            _chunk_item_id(str(row["chunk_id"])),
                            str(row["chunk_id"]),
                            str(row["content_hash"]),
                            indexed_at_ms,
                        )
                        for row in rows
                    ],
                )
                self._clear_pending_hybrid_deletions(conn, point_ids=stale_ids)
                conn.commit()
        except (requests.RequestException, RuntimeError, ValueError) as err:
            emit_structured_log(
                "clawops.context.codebase.hybrid.degraded",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "reason": str(err),
                },
            )
            with self.connect() as conn:
                self._clear_hybrid_state(conn)
                conn.commit()

    def consolidate_runtime_artifacts(self) -> None:
        """Synchronize deferred runtime artifacts such as hybrid vectors."""
        self._sync_hybrid_index()

    def index_with_stats(self) -> IndexStats:
        """Index repository contents into the lexical and graph stores."""
        indexed_files = 0
        skipped_files = 0
        changed_rows: list[tuple[str, str, str, int, int, list[str], str]] = []
        seen_paths: set[str] = set()
        started_at = time.perf_counter()
        with observed_span(
            "clawops.context.codebase.index",
            attributes={
                "repo": self.repo.as_posix(),
                "provider": "codebase",
                "scale": self.scale,
            },
        ) as span:
            with self.connect() as conn:
                existing_metadata = self._load_indexed_metadata(conn)
                for path in self.iter_files():
                    try:
                        stat_result = path.stat()
                    except OSError:
                        continue
                    size_bytes = stat_result.st_size
                    mtime_ns = stat_result.st_mtime_ns
                    rel = path.relative_to(self.repo).as_posix()
                    seen_paths.add(rel)
                    existing = existing_metadata.get(rel)
                    if existing is not None and existing == (mtime_ns, size_bytes):
                        skipped_files += 1
                        continue
                    try:
                        text = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    language = detect_language(path)
                    changed_rows.append(
                        (
                            rel,
                            language,
                            sha256_hex(text),
                            mtime_ns,
                            size_bytes,
                            extract_symbols(path, text),
                            text,
                        )
                    )
                    indexed_files += 1

                stale_paths = set(existing_metadata) - seen_paths
                if stale_paths:
                    stale_point_ids = [
                        str(row["point_id"])
                        for row in conn.execute(
                            f"""
                            SELECT point_id
                            FROM chunk_vectors
                            WHERE path IN ({", ".join("?" for _ in stale_paths)})
                            ORDER BY point_id ASC
                            """,
                            tuple(sorted(stale_paths)),
                        ).fetchall()
                    ]
                    self._queue_hybrid_deletions(conn, point_ids=stale_point_ids)
                    conn.executemany(
                        "DELETE FROM files WHERE path = ?", ((path,) for path in stale_paths)
                    )
                    conn.executemany(
                        "DELETE FROM files_fts WHERE path = ?", ((path,) for path in stale_paths)
                    )
                    conn.executemany(
                        "DELETE FROM chunks WHERE path = ?", ((path,) for path in stale_paths)
                    )
                    conn.executemany(
                        "DELETE FROM chunks_fts WHERE path = ?", ((path,) for path in stale_paths)
                    )
                    conn.executemany(
                        "DELETE FROM chunk_vectors WHERE path = ?",
                        ((path,) for path in stale_paths),
                    )
                    conn.executemany(
                        "DELETE FROM edges WHERE path = ?", ((path,) for path in stale_paths)
                    )

                for rel, language, sha256, mtime_ns, size_bytes, symbols, text in changed_rows:
                    self._queue_hybrid_deletions(
                        conn,
                        point_ids=self._store_changed_file(
                            conn,
                            rel_path=rel,
                            language=language,
                            sha256=sha256,
                            mtime_ns=mtime_ns,
                            size_bytes=size_bytes,
                            symbols=symbols,
                            content=text,
                            all_paths=seen_paths,
                        ),
                    )

                stats = IndexStats(
                    total_files=len(seen_paths),
                    indexed_files=indexed_files,
                    skipped_files=skipped_files,
                    deleted_files=len(stale_paths),
                )
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                snapshot_id = self.index_snapshot_id(conn=conn)
                conn.executemany(
                    """
                    INSERT INTO index_state(key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    [
                        ("chunker_version", str(CHUNKER_VERSION)),
                        ("last_index_completed_ms", str(utc_now_ms())),
                        ("last_index_elapsed_ms", str(elapsed_ms)),
                        ("last_snapshot_id", snapshot_id),
                    ],
                )
                conn.commit()

                features = self._runtime_features()
                if "graph" in features.backend_modes:
                    graph_backend = self._active_graph_backend()
                    graph_backend.upsert(
                        nodes=self._load_graph_nodes(conn),
                        edges=self._load_graph_edges(conn),
                        snapshot_id=snapshot_id,
                    )

            observation: dict[str, bool | int | str] = {
                "repo": self.repo.as_posix(),
                "provider": "codebase",
                "scale": self.scale,
                "total_files": stats.total_files,
                "indexed_files": stats.indexed_files,
                "skipped_files": stats.skipped_files,
                "deleted_files": stats.deleted_files,
                "elapsed_ms": elapsed_ms,
                "hybrid_sync_pending": self._hybrid_requested() and not self._hybrid_state_ready(),
            }
            span.set_attributes(observation)
            emit_structured_log("clawops.context.codebase.index", observation)
            return stats

    def index(self) -> int:
        """Index repository contents into the local store."""
        return self.index_with_stats().total_files

    def _build_search_hit(
        self,
        *,
        path: str,
        content: str,
        symbols_text: str,
        query: str,
        kind: str,
        chunk_id: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> SearchHit:
        """Build a stable search hit with line-oriented snippet context."""
        lines = content.splitlines()
        if not lines:
            return SearchHit(
                path=path,
                snippet="",
                symbols=[],
                start_line=1,
                end_line=1,
                kind=kind,
                chunk_id=chunk_id,
            )
        query_casefold = query.casefold()
        match_line_index: int | None = None
        for index, line in enumerate(lines):
            if query_casefold in line.casefold():
                match_line_index = index
                break
        if match_line_index is None:
            tokens = [token for token in re.split(r"[^A-Za-z0-9_./:-]+", query) if token]
            for token in tokens:
                token_casefold = token.casefold()
                for index, line in enumerate(lines):
                    if token_casefold in line.casefold():
                        match_line_index = index
                        break
                if match_line_index is not None:
                    break
        if match_line_index is None:
            match_line_index = 0
        snippet_start = max(1, match_line_index + 1 - 2)
        snippet_end = min(len(lines), match_line_index + 1 + 2)
        snippet = "\n".join(
            f"{line_number}: {lines[line_number - 1]}"
            for line_number in range(snippet_start, snippet_end + 1)
        )
        symbols = [symbol for symbol in symbols_text.splitlines() if symbol]
        resolved_start = snippet_start if start_line is None else start_line
        resolved_end = snippet_end if end_line is None else end_line
        return SearchHit(
            path=path,
            snippet=snippet,
            symbols=symbols,
            start_line=resolved_start,
            end_line=resolved_end,
            kind=kind,
            chunk_id=chunk_id,
        )

    def _query_files(self, query: str, *, limit: int) -> list[SearchHit]:
        """Run a lexical file-level query."""
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT f.path, f.content, f.symbols
                    FROM files_fts fts
                    JOIN files f ON f.path = fts.path
                    WHERE files_fts MATCH ?
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                rows = conn.execute(
                    """
                    SELECT path, content, symbols
                    FROM files
                    WHERE content LIKE ?
                    LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return [
            self._build_search_hit(
                path=str(row["path"]),
                content=str(row["content"]),
                symbols_text=str(row["symbols"]),
                query=query,
                kind="file",
            )
            for row in rows
        ]

    def _build_chunk_hit(self, row: sqlite3.Row, *, query: str) -> SearchHit:
        """Build one chunk search hit from a SQLite row."""
        symbol = "" if row["symbol"] is None else str(row["symbol"])
        return self._build_search_hit(
            path=str(row["path"]),
            content=str(row["content"]),
            symbols_text=symbol,
            query=query,
            kind=str(row["kind"]),
            chunk_id=str(row["chunk_id"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
        )

    def _query_chunk_rows(self, query: str, *, limit: int) -> list[sqlite3.Row]:
        """Return lexical chunk matches as raw SQLite rows."""
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT chunk_id, path, content, symbol, start_line, end_line, kind
                    FROM chunks_fts
                    JOIN chunks USING(chunk_id)
                    WHERE chunks_fts MATCH ?
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                rows = conn.execute(
                    """
                    SELECT chunk_id, path, content, symbol, start_line, end_line, kind
                    FROM chunks
                    WHERE content LIKE ?
                    LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return list(rows)

    def _query_chunks(self, query: str, *, limit: int) -> list[SearchHit]:
        """Run a lexical chunk-level query."""
        return [
            self._build_chunk_hit(row, query=query)
            for row in self._query_chunk_rows(query, limit=limit)
        ]

    def _load_chunk_rows_by_item_ids(
        self,
        conn: sqlite3.Connection,
        *,
        item_ids: Sequence[int],
    ) -> dict[int, sqlite3.Row]:
        """Load chunk rows keyed by their deterministic vector item IDs."""
        if not item_ids:
            return {}
        placeholders = ", ".join("?" for _ in item_ids)
        rows = conn.execute(
            f"""
            SELECT chunk_vectors.item_id, chunks.chunk_id, chunks.path, chunks.language, chunks.kind,
                   chunks.start_line, chunks.end_line, chunks.symbol, chunks.content, chunks.content_hash
            FROM chunk_vectors
            JOIN chunks USING(chunk_id)
            WHERE chunk_vectors.item_id IN ({placeholders})
            """,
            tuple(item_ids),
        ).fetchall()
        return {int(row["item_id"]): row for row in rows}

    def _dense_chunk_candidates(self, query: str) -> list[DenseSearchCandidate]:
        """Return dense vector candidates for *query* when hybrid mode is healthy."""
        if not self.config.embedding.model or not self.config.embedding.base_url:
            return []
        query_text = query.strip()
        if not query_text:
            return []
        try:
            query_vectors = self._embedding_provider.embed_texts([query_text])
        except Exception as err:
            emit_structured_log(
                "clawops.context.codebase.hybrid.query.degraded",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "lane": "dense",
                    "reason": str(err),
                },
            )
            return []
        if not query_vectors:
            return []
        try:
            return self._vector_backend.search_dense(
                vector=query_vectors[0],
                limit=max(self.config.hybrid.dense_candidate_pool, 1),
                mode="all",
                scope=None,
            )
        except requests.RequestException as err:
            emit_structured_log(
                "clawops.context.codebase.hybrid.query.degraded",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "lane": "dense",
                    "reason": str(err),
                },
            )
            return []

    def _sparse_chunk_candidates(self, query: str) -> list[SparseSearchCandidate]:
        """Return sparse vector candidates for *query* when hybrid mode is healthy."""
        query_text = query.strip()
        if not query_text:
            return []
        with self.connect() as conn:
            encoder = self._load_sparse_encoder(conn)
        if encoder is None:
            return []
        sparse_vector = encoder.encode_query(query_text)
        if sparse_vector.is_empty:
            return []
        try:
            return self._vector_backend.search_sparse(
                vector=sparse_vector.to_qdrant(),
                limit=max(self.config.hybrid.sparse_candidate_pool, 1),
                mode="all",
                scope=None,
            )
        except requests.RequestException as err:
            emit_structured_log(
                "clawops.context.codebase.hybrid.query.degraded",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "lane": "sparse",
                    "reason": str(err),
                },
            )
            return []

    def _rerank_chunk_hits(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Apply optional reranking to the leading chunk hits."""
        if not self.config.rerank.enabled or not hits:
            return hits
        candidate_pool = min(max(self.config.hybrid.rerank_candidate_pool, 0), len(hits))
        if candidate_pool <= 0:
            return hits
        documents = [f"{hit.path}\n{hit.snippet}" for hit in hits[:candidate_pool]]
        try:
            response = self._rerank_provider.score(query, documents)
        except Exception as err:
            if not self.config.rerank.fail_open:
                raise
            emit_structured_log(
                "clawops.context.codebase.rerank.fail_open",
                {
                    "repo": self.repo.as_posix(),
                    "scale": self.scale,
                    "reason": str(err),
                },
            )
            return hits
        if not response.applied:
            return hits
        if len(response.scores) != candidate_pool:
            raise ValueError("rerank response count does not match the candidate pool size")
        reranked = list(zip(hits[:candidate_pool], response.scores, strict=True))
        reranked.sort(key=lambda item: item[1], reverse=True)
        return [item[0] for item in reranked] + hits[candidate_pool:]

    def _query_chunks_hybrid(self, query: str, *, limit: int) -> list[SearchHit]:
        """Run hybrid chunk retrieval for medium and large scales."""
        lexical_pool = max(limit, self.config.hybrid.sparse_candidate_pool, 1)
        lexical_rows = self._query_chunk_rows(query, limit=lexical_pool)
        dense_candidates = self._dense_chunk_candidates(query)
        sparse_candidates = self._sparse_chunk_candidates(query)
        item_ids = [
            *(candidate.item_id for candidate in dense_candidates),
            *(candidate.item_id for candidate in sparse_candidates),
        ]
        with self.connect() as conn:
            rows_by_item_id = self._load_chunk_rows_by_item_ids(conn, item_ids=item_ids)

        merged: dict[str, HybridRankedChunk] = {}
        lexical_weight = self.config.hybrid.text_weight / 2.0
        sparse_weight = self.config.hybrid.text_weight / 2.0
        dense_weight = self.config.hybrid.vector_weight

        for rank, row in enumerate(lexical_rows, start=1):
            chunk_id = str(row["chunk_id"])
            entry = merged.setdefault(chunk_id, HybridRankedChunk(row=row))
            entry.score += _rank_contribution(
                rank=rank,
                weight=lexical_weight,
                hybrid=self.config.hybrid,
            )

        for rank, dense_candidate in enumerate(dense_candidates, start=1):
            dense_row = rows_by_item_id.get(dense_candidate.item_id)
            if dense_row is None:
                continue
            chunk_id = str(dense_row["chunk_id"])
            entry = merged.setdefault(chunk_id, HybridRankedChunk(row=dense_row))
            entry.score += _rank_contribution(
                rank=rank,
                weight=dense_weight,
                hybrid=self.config.hybrid,
            )

        for rank, sparse_candidate in enumerate(sparse_candidates, start=1):
            sparse_row = rows_by_item_id.get(sparse_candidate.item_id)
            if sparse_row is None:
                continue
            chunk_id = str(sparse_row["chunk_id"])
            entry = merged.setdefault(chunk_id, HybridRankedChunk(row=sparse_row))
            entry.score += _rank_contribution(
                rank=rank,
                weight=sparse_weight,
                hybrid=self.config.hybrid,
            )

        ranked_rows = sorted(
            merged.values(),
            key=lambda item: (-item.score, str(item.row["path"])),
        )
        hits = [self._build_chunk_hit(item.row, query=query) for item in ranked_rows]
        reranked_hits = self._rerank_chunk_hits(query, hits)
        return reranked_hits[:limit]

    def query(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        """Run a scale-aware query."""
        if self.scale == "small":
            return self._query_files(query, limit=limit)
        if "hybrid" in self.backend_modes():
            return self._query_chunks_hybrid(query, limit=limit)
        return self._query_chunks(query, limit=limit)

    def load_file_records(self, paths: Sequence[str]) -> list[IndexedFile]:
        """Load indexed file metadata for repo-relative paths."""
        if not paths:
            return []
        placeholders = ", ".join("?" for _ in paths)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT path, sha256, size_bytes, symbols, content
                FROM files
                WHERE path IN ({placeholders})
                ORDER BY path ASC
                """,
                tuple(paths),
            ).fetchall()
        records: list[IndexedFile] = []
        for row in rows:
            symbols = tuple(item for item in str(row["symbols"]).splitlines() if item.strip())
            records.append(
                IndexedFile(
                    path=str(row["path"]),
                    sha256=str(row["sha256"]),
                    size_bytes=int(row["size_bytes"]),
                    symbols=symbols,
                    content=str(row["content"]),
                )
            )
        return records

    def snapshot_records(self) -> list[IndexedFile]:
        """Return the full indexed file snapshot in stable order."""
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT path, sha256, size_bytes, symbols, content
                FROM files
                ORDER BY path ASC
                """).fetchall()
        records: list[IndexedFile] = []
        for row in rows:
            symbols = tuple(item for item in str(row["symbols"]).splitlines() if item.strip())
            records.append(
                IndexedFile(
                    path=str(row["path"]),
                    sha256=str(row["sha256"]),
                    size_bytes=int(row["size_bytes"]),
                    symbols=symbols,
                    content=str(row["content"]),
                )
            )
        return records

    def index_snapshot_id(self, *, conn: sqlite3.Connection | None = None) -> str:
        """Return a stable hash of the indexed repository snapshot."""
        if conn is None:
            records = self.snapshot_records()
        else:
            rows = conn.execute("""
                SELECT path, sha256, size_bytes
                FROM files
                ORDER BY path ASC
                """).fetchall()
            records = [
                IndexedFile(
                    path=str(row["path"]),
                    sha256=str(row["sha256"]),
                    size_bytes=int(row["size_bytes"]),
                    symbols=(),
                    content="",
                )
                for row in rows
            ]
        snapshot = [
            {"path": record.path, "sha256": record.sha256, "size_bytes": record.size_bytes}
            for record in records
        ]
        return sha256_hex(canonical_json(snapshot))

    def _load_graph_nodes(self, conn: sqlite3.Connection) -> list[GraphNode]:
        """Load graph nodes from indexed files."""
        rows = conn.execute("SELECT path, language FROM files ORDER BY path ASC").fetchall()
        return [
            GraphNode(
                node_id=f"path:{str(row['path'])}",
                path=str(row["path"]),
                language=str(row["language"]),
                kind="file",
            )
            for row in rows
        ]

    def _load_graph_edges(self, conn: sqlite3.Connection) -> list[EdgeRecord]:
        """Load graph edges from SQLite."""
        rows = conn.execute("""
            SELECT src_id, dst_id, edge_type, path, weight
            FROM edges
            ORDER BY src_id ASC, dst_id ASC, edge_type ASC
            """).fetchall()
        return [
            EdgeRecord(
                src_id=str(row["src_id"]),
                dst_id=str(row["dst_id"]),
                edge_type=str(row["edge_type"]),
                path=None if row["path"] is None else str(row["path"]),
                weight=int(row["weight"]),
            )
            for row in rows
        ]

    def _dependency_expansion(self, paths: Sequence[str]) -> list[str]:
        """Return related dependency paths for the current query result."""
        features = self._runtime_features()
        if "graph" not in features.backend_modes:
            return []
        backend = self._active_graph_backend()
        expanded: list[str] = []
        seen: set[str] = set(paths)
        for path in paths:
            neighbors = backend.neighbors(
                node_id=f"path:{path}",
                edge_types=("IMPORTS",),
                depth=self.config.graph.depth,
                limit=self.config.graph.limit,
            )
            for neighbor in neighbors:
                rel_path = neighbor.removeprefix("path:")
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                expanded.append(rel_path)
        return expanded

    def git_diff(self) -> str:
        """Return the current git diff, if any."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), "diff", "--no-ext-diff", "--unified=0"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ""
        return result.stdout.strip()

    def git_diff_paths(self) -> tuple[str, ...]:
        """Return the repo-relative paths touched by the current git diff."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), "diff", "--name-only", "--no-ext-diff"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ()
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())

    def pack(self, query: str, *, limit: int = 8) -> str:
        """Create a stable markdown context pack."""
        hits = self.query(query, limit=limit)
        dependency_paths = self._dependency_expansion([hit.path for hit in hits])
        dependency_records = self.load_file_records(dependency_paths)
        with self.connect() as conn:
            index_state = self._load_index_state(conn)
        lines: list[str] = ["# Repo Context Pack", ""]
        lines.append("- provider: codebase")
        lines.append(f"- scale: {self.scale}")
        lines.append(f"- snapshot_id: {self.index_snapshot_id()}")
        lines.append(f"- index_time_ms: {index_state.get('last_index_elapsed_ms', '0')}")
        lines.append(f"- backend_modes: {', '.join(self.backend_modes())}")
        lines.append(f"- repo_root: {self.repo}")
        lines.append(f"- query: {query}")
        lines.append("")

        diff = self.git_diff()
        if diff:
            lines.append("## Active diff")
            diff_paths = self.git_diff_paths()
            if diff_paths:
                lines.append(f"- affected_files: {', '.join(diff_paths)}")
            lines.append("```diff")
            lines.append(diff[:8000])
            lines.append("```")
            lines.append("")

        lines.append("## Top retrieved chunks" if self.scale != "small" else "## Retrieved files")
        for hit in hits:
            lines.append(f"### {hit.path}")
            lines.append(f"- kind: {hit.kind}")
            lines.append(f"- lines: {hit.start_line}-{hit.end_line}")
            if hit.symbols:
                lines.append(f"- symbols: {', '.join(hit.symbols[:12])}")
            if hit.chunk_id is not None:
                lines.append(f"- chunk_id: {hit.chunk_id}")
            lines.append("```text")
            lines.append(hit.snippet)
            lines.append("```")
            lines.append("")

        if dependency_records:
            lines.append("## Dependency expansion")
            for record in dependency_records:
                lines.append(f"### {record.path}")
                if record.symbols:
                    lines.append(f"- symbols: {', '.join(record.symbols[:12])}")
                lines.append(f"- sha256: {record.sha256}")
                lines.append("```text")
                lines.append(record.content[:1600])
                lines.append("```")
                lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def load_config(path: pathlib.Path) -> CodebaseContextConfig:
    """Load and validate the codebase context YAML config."""
    config = _as_mapping("codebase context", load_yaml(path))
    index = _as_mapping("index", config.get("index"))
    paths = _as_mapping("paths", config.get("paths"))
    graph = _as_mapping("graph", config.get("graph"))
    embedding = _as_mapping("embedding", config.get("embedding"))
    rerank = _as_mapping("rerank", config.get("rerank"))
    rerank_local = _as_mapping("rerank.local", rerank.get("local"))
    rerank_http = _as_mapping("rerank.compatible_http", rerank.get("compatible_http"))
    hybrid = _as_mapping("hybrid", config.get("hybrid"))
    qdrant = _as_mapping("qdrant", config.get("qdrant"))
    db_path = index.get("db_path", ".clawops/context.sqlite")
    if not isinstance(db_path, str):
        raise TypeError("index.db_path must be a string")
    include = _as_string_list("paths.include", paths.get("include"))
    exclude = _as_string_list("paths.exclude", paths.get("exclude"))
    default_embedding = EmbeddingConfig(enabled=False, provider="disabled")
    default_rerank = RerankConfig(enabled=False, provider="none", fallback_provider="none")
    default_hybrid = HybridConfig()
    default_qdrant = QdrantConfig(enabled=False, collection=DEFAULT_CODEBASE_QDRANT_COLLECTION)
    return CodebaseContextConfig(
        db_path=pathlib.Path(db_path),
        include_globs=tuple(include),
        exclude_globs=tuple(exclude),
        max_file_size_bytes=_as_positive_int(
            "index.max_file_size_bytes",
            index.get("max_file_size_bytes"),
            default=1_000_000,
        ),
        include_hidden=_as_bool(
            "index.include_hidden",
            index.get("include_hidden"),
            default=False,
        ),
        symlink_policy=_as_symlink_policy(
            "index.symlink_policy",
            index.get("symlink_policy"),
            default="in_repo_only",
        ),
        graph=GraphConfig(
            enabled=_as_bool("graph.enabled", graph.get("enabled"), default=True),
            backend=_as_graph_backend("graph.backend", graph.get("backend"), default="neo4j"),
            allow_degraded_fallback=_as_bool(
                "graph.allow_degraded_fallback",
                graph.get("allow_degraded_fallback"),
                default=True,
            ),
            neo4j_url=_as_string(
                "graph.neo4j_url",
                graph.get("neo4j_url"),
                default="http://127.0.0.1:7474",
            ),
            neo4j_username_env=_as_string(
                "graph.neo4j_username_env",
                graph.get("neo4j_username_env"),
                default="NEO4J_USERNAME",
            ),
            neo4j_password_env=_as_string(
                "graph.neo4j_password_env",
                graph.get("neo4j_password_env"),
                default="NEO4J_PASSWORD",
            ),
            database=_as_string("graph.database", graph.get("database"), default="neo4j"),
            depth=_as_positive_int("graph.depth", graph.get("depth"), default=1),
            limit=_as_positive_int("graph.limit", graph.get("limit"), default=12),
        ),
        embedding=EmbeddingConfig(
            enabled=_as_bool("embedding.enabled", embedding.get("enabled"), default=False),
            provider=_as_embedding_provider(
                "embedding.provider",
                embedding.get("provider"),
                default=default_embedding.provider,
            ),
            model=_as_env_string("embedding.model", embedding.get("model"), default=""),
            base_url=_as_env_string("embedding.base_url", embedding.get("base_url"), default=""),
            api_key_env=_as_optional_env_string(
                "embedding.api_key_env", embedding.get("api_key_env")
            ),
            api_key=_as_optional_env_string("embedding.api_key", embedding.get("api_key")),
            dimensions=_as_optional_positive_int(
                "embedding.dimensions", embedding.get("dimensions")
            ),
            batch_size=_as_positive_int(
                "embedding.batch_size",
                embedding.get("batch_size"),
                default=default_embedding.batch_size,
            ),
            timeout_ms=_as_positive_int(
                "embedding.timeout_ms",
                embedding.get("timeout_ms"),
                default=default_embedding.timeout_ms,
            ),
        ),
        rerank=RerankConfig(
            enabled=_as_bool("rerank.enabled", rerank.get("enabled"), default=False),
            provider=_as_rerank_provider(
                "rerank.provider",
                rerank.get("provider"),
                default=default_rerank.provider,
            ),
            fallback_provider=_as_rerank_provider(
                "rerank.fallback_provider",
                rerank.get("fallback_provider"),
                default=default_rerank.fallback_provider,
            ),
            fail_open=_as_bool(
                "rerank.fail_open",
                rerank.get("fail_open"),
                default=default_rerank.fail_open,
            ),
            normalize_scores=_as_bool(
                "rerank.normalize_scores",
                rerank.get("normalize_scores"),
                default=default_rerank.normalize_scores,
            ),
            local=dataclasses.replace(
                default_rerank.local,
                model=_as_env_string("rerank.local.model", rerank_local.get("model"), default=""),
                batch_size=_as_positive_int(
                    "rerank.local.batch_size",
                    rerank_local.get("batch_size"),
                    default=default_rerank.local.batch_size,
                ),
                max_length=_as_positive_int(
                    "rerank.local.max_length",
                    rerank_local.get("max_length"),
                    default=default_rerank.local.max_length,
                ),
                device=_as_env_string(
                    "rerank.local.device",
                    rerank_local.get("device"),
                    default=default_rerank.local.device,
                ),
            ),
            compatible_http=dataclasses.replace(
                default_rerank.compatible_http,
                model=_as_env_string(
                    "rerank.compatible_http.model",
                    rerank_http.get("model"),
                    default="",
                ),
                base_url=_as_env_string(
                    "rerank.compatible_http.base_url",
                    rerank_http.get("base_url"),
                    default="",
                ),
                api_key_env=_as_optional_env_string(
                    "rerank.compatible_http.api_key_env",
                    rerank_http.get("api_key_env"),
                ),
                api_key=_as_optional_env_string(
                    "rerank.compatible_http.api_key",
                    rerank_http.get("api_key"),
                ),
                timeout_ms=_as_positive_int(
                    "rerank.compatible_http.timeout_ms",
                    rerank_http.get("timeout_ms"),
                    default=default_rerank.compatible_http.timeout_ms,
                ),
            ),
        ),
        hybrid=HybridConfig(
            dense_candidate_pool=_as_positive_int(
                "hybrid.dense_candidate_pool",
                hybrid.get("dense_candidate_pool"),
                default=default_hybrid.dense_candidate_pool,
            ),
            sparse_candidate_pool=_as_positive_int(
                "hybrid.sparse_candidate_pool",
                hybrid.get("sparse_candidate_pool"),
                default=default_hybrid.sparse_candidate_pool,
            ),
            vector_weight=_as_float(
                "hybrid.vector_weight",
                hybrid.get("vector_weight"),
                default=default_hybrid.vector_weight,
            ),
            text_weight=_as_float(
                "hybrid.text_weight",
                hybrid.get("text_weight"),
                default=default_hybrid.text_weight,
            ),
            fusion=_as_fusion_mode(
                "hybrid.fusion", hybrid.get("fusion"), default=default_hybrid.fusion
            ),
            rrf_k=_as_positive_int(
                "hybrid.rrf_k", hybrid.get("rrf_k"), default=default_hybrid.rrf_k
            ),
            rerank_candidate_pool=_as_positive_int(
                "hybrid.rerank_candidate_pool",
                hybrid.get("rerank_candidate_pool"),
                default=default_hybrid.rerank_candidate_pool,
            ),
        ),
        qdrant=QdrantConfig(
            enabled=_as_bool("qdrant.enabled", qdrant.get("enabled"), default=False),
            url=_as_env_string("qdrant.url", qdrant.get("url"), default=default_qdrant.url),
            collection=_as_env_string(
                "qdrant.collection",
                qdrant.get("collection"),
                default=DEFAULT_CODEBASE_QDRANT_COLLECTION,
            ),
            dense_vector_name=_as_env_string(
                "qdrant.dense_vector_name",
                qdrant.get("dense_vector_name"),
                default=default_qdrant.dense_vector_name,
            ),
            sparse_vector_name=_as_env_string(
                "qdrant.sparse_vector_name",
                qdrant.get("sparse_vector_name"),
                default=default_qdrant.sparse_vector_name,
            ),
            timeout_ms=_as_positive_int(
                "qdrant.timeout_ms",
                qdrant.get("timeout_ms"),
                default=default_qdrant.timeout_ms,
            ),
            api_key_env=_as_optional_env_string("qdrant.api_key_env", qdrant.get("api_key_env")),
            api_key=_as_optional_env_string("qdrant.api_key", qdrant.get("api_key")),
        ),
    )


def service_from_config(
    config_path: pathlib.Path,
    repo: pathlib.Path,
    *,
    scale: ContextScale,
) -> CodebaseContextService:
    """Build a codebase context service from a YAML config."""
    config = load_config(config_path)
    db_path = config.db_path
    if not db_path.is_absolute():
        db_path = repo / db_path
    return CodebaseContextService(
        repo,
        dataclasses.replace(config, db_path=db_path),
        scale=validate_context_scale(scale, path="scale"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse codebase context CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("index", "query", "pack"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True, type=pathlib.Path)
        cmd.add_argument("--repo", required=True, type=pathlib.Path)
        cmd.add_argument("--scale", required=True, choices=("small", "medium", "large"))

    index = sub.choices["index"]
    index.add_argument("--json", action="store_true")

    query = sub.choices["query"]
    query.add_argument("--query", required=True)
    query.add_argument("--limit", type=int, default=8)

    pack = sub.choices["pack"]
    pack.add_argument("--query", required=True)
    pack.add_argument("--limit", type=int, default=8)
    pack.add_argument("--output", required=True, type=pathlib.Path)

    worker = sub.add_parser("worker")
    worker.add_argument("--config", required=True, type=pathlib.Path)
    worker.add_argument("--repo", required=True, type=pathlib.Path)
    worker.add_argument("--scale", required=True, choices=("small", "medium", "large"))
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval-seconds", type=int, default=30)
    worker.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    service = service_from_config(
        args.config, expand(args.repo), scale=cast(ContextScale, args.scale)
    )
    if args.command == "index":
        stats = service.index_with_stats()
        if args.json:
            print(json.dumps(stats.to_dict(), sort_keys=True))
        else:
            print(
                " ".join(
                    (
                        f"indexed={stats.total_files}",
                        f"changed={stats.indexed_files}",
                        f"skipped={stats.skipped_files}",
                        f"deleted={stats.deleted_files}",
                    )
                )
            )
        return 0
    if args.command == "query":
        hits = service.query(args.query, limit=args.limit)
        for hit in hits:
            print(f"{hit.path}\t{','.join(hit.symbols[:8])}\t{hit.start_line}-{hit.end_line}")
        return 0
    if args.command == "pack":
        output = service.pack(args.query, limit=args.limit)
        write_text(args.output, output)
        print(args.output)
        return 0
    if args.command == "worker":
        if args.interval_seconds <= 0:
            raise ValueError("--interval-seconds must be positive")
        while True:
            stats = service.index_with_stats()
            service.consolidate_runtime_artifacts()
            if args.json:
                print(json.dumps(stats.to_dict(), sort_keys=True))
            else:
                print(
                    " ".join(
                        (
                            f"indexed={stats.total_files}",
                            f"changed={stats.indexed_files}",
                            f"skipped={stats.skipped_files}",
                            f"deleted={stats.deleted_files}",
                        )
                    )
                )
            if args.once:
                return 0
            time.sleep(args.interval_seconds)
    raise AssertionError("unreachable")
