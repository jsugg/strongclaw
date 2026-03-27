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


@dataclasses.dataclass(frozen=True, slots=True)
class CodebaseContextFeatures:
    """Resolved runtime features for one scale invocation."""

    scale: ContextScale
    backend_modes: tuple[str, ...]
    graph_backend: GraphBackendName
    degraded_graph: bool = False


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

    def _graph_features(self) -> CodebaseContextFeatures:
        """Resolve the active runtime feature set."""
        if self.scale == "small" or not self.config.graph.enabled:
            return CodebaseContextFeatures(
                scale=self.scale,
                backend_modes=("lexical",),
                graph_backend="sqlite",
                degraded_graph=False,
            )
        if self.config.graph.backend == "neo4j":
            health = self._neo4j_graph_backend.health()
            if bool(health.get("healthy", False)):
                return CodebaseContextFeatures(
                    scale=self.scale,
                    backend_modes=("lexical", "graph"),
                    graph_backend="neo4j",
                    degraded_graph=False,
                )
            if self.scale == "large" and not self.config.graph.allow_degraded_fallback:
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
            return CodebaseContextFeatures(
                scale=self.scale,
                backend_modes=("lexical", "graph"),
                graph_backend="sqlite",
                degraded_graph=True,
            )
        return CodebaseContextFeatures(
            scale=self.scale,
            backend_modes=("lexical", "graph"),
            graph_backend="sqlite",
            degraded_graph=False,
        )

    def backend_modes(self) -> tuple[str, ...]:
        """Return the active retrieval mode names."""
        return self._graph_features().backend_modes

    def _active_graph_backend(self) -> GraphBackend:
        features = self._graph_features()
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
    ) -> None:
        """Persist file, chunk, and edge state for one changed file."""
        symbols_text = "\n".join(symbols)
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
                        "DELETE FROM edges WHERE path = ?", ((path,) for path in stale_paths)
                    )

                for rel, language, sha256, mtime_ns, size_bytes, symbols, text in changed_rows:
                    self._store_changed_file(
                        conn,
                        rel_path=rel,
                        language=language,
                        sha256=sha256,
                        mtime_ns=mtime_ns,
                        size_bytes=size_bytes,
                        symbols=symbols,
                        content=text,
                        all_paths=seen_paths,
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

                features = self._graph_features()
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

    def _query_chunks(self, query: str, *, limit: int) -> list[SearchHit]:
        """Run a lexical chunk-level query."""
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
        hits: list[SearchHit] = []
        for row in rows:
            symbol = "" if row["symbol"] is None else str(row["symbol"])
            hits.append(
                self._build_search_hit(
                    path=str(row["path"]),
                    content=str(row["content"]),
                    symbols_text=symbol,
                    query=query,
                    kind=str(row["kind"]),
                    chunk_id=str(row["chunk_id"]),
                    start_line=int(row["start_line"]),
                    end_line=int(row["end_line"]),
                )
            )
        return hits

    def query(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        """Run a scale-aware lexical query."""
        if self.scale == "small":
            return self._query_files(query, limit=limit)
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
        features = self._graph_features()
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
    db_path = index.get("db_path", ".clawops/context.sqlite")
    if not isinstance(db_path, str):
        raise TypeError("index.db_path must be a string")
    include = _as_string_list("paths.include", paths.get("include"))
    exclude = _as_string_list("paths.exclude", paths.get("exclude"))
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
