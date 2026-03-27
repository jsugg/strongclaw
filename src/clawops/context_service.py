"""Repository indexing and lexical context packing service."""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import pathlib
import re
import sqlite3
import subprocess
import time
from collections.abc import Iterable, Sequence
from typing import Literal, cast

from clawops.common import canonical_json, expand, load_yaml, sha256_hex, utc_now_ms, write_text
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

type SymlinkPolicy = Literal["follow", "in_repo_only", "never"]


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


def _as_mapping(name: str, value: object) -> dict[str, object]:
    """Validate a mapping-shaped config section."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{name} config must be a mapping")
    return cast(dict[str, object], value)


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


@dataclasses.dataclass(frozen=True, slots=True)
class IndexedFile:
    """Indexed file metadata used by context envelopes."""

    path: str
    sha256: str
    size_bytes: int
    symbols: tuple[str, ...]
    content: str


@dataclasses.dataclass(frozen=True, slots=True)
class ContextConfig:
    """Context-service configuration."""

    db_path: pathlib.Path
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_file_size_bytes: int = 1_000_000
    include_hidden: bool = False
    symlink_policy: SymlinkPolicy = "in_repo_only"


class ContextService:
    """Lexical repo indexer and query engine."""

    def __init__(self, repo: pathlib.Path, config: ContextConfig) -> None:
        self.repo = repo.expanduser().resolve()
        self.config = config
        self.db_path = config.db_path.expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        """Open the sqlite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Apply additive schema migrations for existing context databases."""
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(files)").fetchall()}
        for column, definition in MIGRATION_COLUMNS.items():
            if column in columns:
                continue
            conn.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")
        conn.commit()

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
        """Load the stored file metadata used for incremental skip decisions."""
        rows = conn.execute("SELECT path, mtime_ns, size_bytes FROM files").fetchall()
        return {str(row["path"]): (int(row["mtime_ns"]), int(row["size_bytes"])) for row in rows}

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

    def index_with_stats(self) -> IndexStats:
        """Index repository contents into the lexical store."""
        indexed_files = 0
        skipped_files = 0
        seen_paths: set[str] = set()
        started_at = time.perf_counter()
        with observed_span(
            "clawops.context.index", attributes={"repo": self.repo.as_posix()}
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
                    rel = str(path.relative_to(self.repo))
                    seen_paths.add(rel)
                    existing = existing_metadata.get(rel)
                    if existing is not None and existing == (mtime_ns, size_bytes):
                        skipped_files += 1
                        continue
                    try:
                        text = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    sha = sha256_hex(text)
                    symbols = extract_symbols(path, text)
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
                        (
                            rel,
                            sha,
                            detect_language(path),
                            mtime_ns,
                            size_bytes,
                            "\n".join(symbols),
                            text,
                        ),
                    )
                    conn.execute("DELETE FROM files_fts WHERE path = ?", (rel,))
                    conn.execute(
                        "INSERT INTO files_fts(path, content, symbols) VALUES (?, ?, ?)",
                        (rel, text, "\n".join(symbols)),
                    )
                    indexed_files += 1
                indexed_paths = set(existing_metadata)
                stale_paths = indexed_paths - seen_paths
                if stale_paths:
                    conn.executemany(
                        "DELETE FROM files WHERE path = ?", ((path,) for path in stale_paths)
                    )
                    conn.executemany(
                        "DELETE FROM files_fts WHERE path = ?", ((path,) for path in stale_paths)
                    )
                conn.commit()
            stats = IndexStats(
                total_files=len(seen_paths),
                indexed_files=indexed_files,
                skipped_files=skipped_files,
                deleted_files=len(stale_paths),
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observation: dict[str, bool | int | float | str] = {
                "repo": self.repo.as_posix(),
                "total_files": stats.total_files,
                "indexed_files": stats.indexed_files,
                "skipped_files": stats.skipped_files,
                "deleted_files": stats.deleted_files,
                "elapsed_ms": elapsed_ms,
            }
            span.set_attributes(observation)
            emit_structured_log("clawops.context.index", observation)
            return stats

    def index(self) -> int:
        """Index repository contents into the lexical store."""
        return self.index_with_stats().total_files

    def _build_search_hit(
        self, *, path: str, content: str, symbols_text: str, query: str
    ) -> SearchHit:
        """Build a stable search hit with line-oriented snippet context."""
        lines = content.splitlines()
        if not lines:
            return SearchHit(path=path, snippet="", symbols=[], start_line=1, end_line=1)
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
        start_line = max(1, match_line_index + 1 - 2)
        end_line = min(len(lines), match_line_index + 1 + 2)
        snippet = "\n".join(
            f"{line_number}: {lines[line_number - 1]}"
            for line_number in range(start_line, end_line + 1)
        )
        symbols = [symbol for symbol in symbols_text.splitlines() if symbol]
        return SearchHit(
            path=path,
            snippet=snippet,
            symbols=symbols,
            start_line=start_line,
            end_line=end_line,
        )

    def query(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        """Run a lexical query.

        SQLite FTS tokenization can miss identifiers with underscores or language-
        specific punctuation, so the implementation falls back to a substring
        scan when the FTS query returns no rows.
        """
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
        hits: list[SearchHit] = []
        for row in rows:
            hits.append(
                self._build_search_hit(
                    path=str(row["path"]),
                    content=str(row["content"]),
                    symbols_text=str(row["symbols"]),
                    query=query,
                )
            )
        return hits

    def load_file_records(self, paths: Sequence[str]) -> list[IndexedFile]:
        """Load indexed file metadata for one or more repo-relative paths."""
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

    def index_snapshot_id(self) -> str:
        """Return a stable hash of the indexed repository snapshot."""
        snapshot = [
            {
                "path": record.path,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            }
            for record in self.snapshot_records()
        ]
        return sha256_hex(canonical_json(snapshot))

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
        lines: list[str] = []
        lines.append("# Repo Context Pack")
        lines.append("")
        lines.append(f"- generated_at_ms: {utc_now_ms()}")
        lines.append(f"- repo: {self.repo}")
        lines.append(f"- query: {query}")
        lines.append("")
        diff = self.git_diff()
        if diff:
            lines.append("## Current diff")
            lines.append("```diff")
            lines.append(diff[:8000])
            lines.append("```")
            lines.append("")
        lines.append("## Retrieved files")
        for hit in hits:
            lines.append(f"### {hit.path}")
            if hit.symbols:
                lines.append(f"- symbols: {', '.join(hit.symbols[:12])}")
            lines.append(f"- lines: {hit.start_line}-{hit.end_line}")
            lines.append("```text")
            lines.append(hit.snippet)
            lines.append("```")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def load_config(path: pathlib.Path) -> ContextConfig:
    """Load and validate the context-service YAML config."""
    config = _as_mapping("context service", load_yaml(path))
    index = _as_mapping("index", config.get("index"))
    paths = _as_mapping("paths", config.get("paths"))
    db_path = index.get("db_path", ".clawops/context.sqlite")
    if not isinstance(db_path, str):
        raise TypeError("index.db_path must be a string")
    include = _as_string_list("paths.include", paths.get("include"))
    exclude = _as_string_list("paths.exclude", paths.get("exclude"))
    return ContextConfig(
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
    )


def service_from_config(config_path: pathlib.Path, repo: pathlib.Path) -> ContextService:
    """Build a ContextService from a YAML config."""
    config = load_config(config_path)
    db_path = config.db_path
    if not db_path.is_absolute():
        db_path = repo / db_path
    return ContextService(repo, dataclasses.replace(config, db_path=db_path))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse context CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("index", "query", "pack"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True, type=pathlib.Path)
        cmd.add_argument("--repo", required=True, type=pathlib.Path)

    index = sub.choices["index"]
    index.add_argument("--json", action="store_true")

    query = sub.choices["query"]
    query.add_argument("--query", required=True)
    query.add_argument("--limit", type=int, default=8)

    pack = sub.choices["pack"]
    pack.add_argument("--query", required=True)
    pack.add_argument("--limit", type=int, default=8)
    pack.add_argument("--output", required=True, type=pathlib.Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    service = service_from_config(args.config, expand(args.repo))
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
            print(f"{hit.path}\t{','.join(hit.symbols[:8])}")
        return 0
    if args.command == "pack":
        output = service.pack(args.query, limit=args.limit)
        write_text(args.output, output)
        print(args.output)
        return 0
    raise AssertionError("unreachable")
