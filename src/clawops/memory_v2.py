"""Strongclaw memory v2 engine and CLI."""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import pathlib
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from clawops.common import ensure_parent, load_yaml, write_text

DEFAULT_MEMORY_FILE_NAMES = ("MEMORY.md", "memory.md")
DEFAULT_DAILY_DIR = "memory"
DEFAULT_BANK_DIR = "bank"
DEFAULT_DB_PATH = ".openclaw/memory-v2.sqlite"
DEFAULT_SNIPPET_CHARS = 400
DEFAULT_SEARCH_RESULTS = 8
HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")
ENTITY_TAG_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_.-]*)")
RETAIN_HEADER_RE = re.compile(r"^#{1,6}\s+retain\s*$", re.IGNORECASE)
TYPED_ENTRY_RE = re.compile(
    r"^(?P<kind>Fact|Reflection|Entity(?:\[(?P<entity>[^\]]+)\])?|Opinion(?:\[c=(?P<confidence>\d(?:\.\d+)?)\])?)"
    r":\s*(?P<text>.+?)\s*$",
    re.IGNORECASE,
)
WRITABLE_PREFIXES = ("memory/", "bank/")
BANK_HEADERS = {
    "fact": "# World Model\n\n## Entries\n",
    "reflection": "# Experience\n\n## Entries\n",
    "opinion": "# Opinions\n\n## Entries\n",
}
ENTRY_LABELS = {
    "fact": "Fact",
    "reflection": "Reflection",
    "opinion": "Opinion",
    "entity": "Entity",
}
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        rel_path TEXT NOT NULL UNIQUE,
        abs_path TEXT NOT NULL,
        lane TEXT NOT NULL,
        source_name TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        line_count INTEGER NOT NULL,
        indexed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_items (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        lane TEXT NOT NULL,
        source_name TEXT NOT NULL,
        item_type TEXT NOT NULL,
        title TEXT NOT NULL,
        snippet TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        confidence REAL,
        entities_json TEXT NOT NULL
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS search_items_fts USING fts5(
        title,
        snippet,
        entities,
        tokenize = 'unicode61'
    )
    """,
)

Lane = Literal["memory", "corpus"]
EntryType = Literal["fact", "reflection", "opinion", "entity", "paragraph", "section"]


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusPathConfig:
    """Additional Markdown corpus path to index."""

    name: str
    path: pathlib.Path
    pattern: str


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryV2Config:
    """Validated memory v2 configuration."""

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


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedItem:
    """Single indexed search item."""

    item_type: EntryType
    title: str
    snippet: str
    start_line: int
    end_line: int
    confidence: float | None = None
    entities: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class IndexedDocument:
    """Materialized document ready to persist into the derived index."""

    rel_path: str
    abs_path: pathlib.Path
    lane: Lane
    source_name: str
    sha256: str
    line_count: int
    items: tuple[ParsedItem, ...]


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
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.entities:
            payload["entities"] = list(self.entities)
        return payload


@dataclasses.dataclass(frozen=True, slots=True)
class ReindexSummary:
    """Summary of a reindex run."""

    files: int
    chunks: int
    dirty: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert the summary to a serializable dictionary."""
        return {"files": self.files, "chunks": self.chunks, "dirty": self.dirty}


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


def _as_string_list(name: str, value: object, *, default: Sequence[str]) -> tuple[str, ...]:
    """Validate a list of strings."""
    if value is None:
        return tuple(default)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise TypeError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


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


def _resolve_path(base_dir: pathlib.Path, raw_path: str) -> pathlib.Path:
    """Resolve a config path relative to *base_dir*."""
    path = pathlib.Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _resolve_under_workspace(workspace_root: pathlib.Path, path: pathlib.Path) -> str:
    """Return *path* relative to the workspace root."""
    try:
        return path.resolve().relative_to(workspace_root).as_posix()
    except ValueError as err:
        raise ValueError(f"{path} must stay within {workspace_root}") from err


def _matches_glob(path_text: str, pattern: str) -> bool:
    """Match a relative path against a repo-style glob."""
    return fnmatch.fnmatch(path_text, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(path_text, pattern[3:])
    )


def default_config_path() -> pathlib.Path:
    """Return the shipped default memory-v2 config path."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "platform/configs/memory/memory-v2.yaml"


def load_config(path: pathlib.Path) -> MemoryV2Config:
    """Load and validate a memory-v2 config file."""
    raw = load_yaml(path)
    root = _as_mapping("memory-v2 config", raw)
    config_dir = path.resolve().parent

    storage = _as_mapping("storage", root.get("storage") or {})
    workspace = _as_mapping("workspace", root.get("workspace") or {})
    corpus = _as_mapping("corpus", root.get("corpus") or {})
    limits = _as_mapping("limits", root.get("limits") or {})

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
            _resolve_under_workspace(workspace_root, resolved_path)
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
            limits.get("max_snippet_chars"),
            default=DEFAULT_SNIPPET_CHARS,
        ),
        default_max_results=_as_positive_int(
            "limits.default_max_results",
            limits.get("default_max_results"),
            default=DEFAULT_SEARCH_RESULTS,
        ),
    )


class MemoryV2Engine:
    """Markdown-canonical memory engine with a derived SQLite index."""

    def __init__(self, config: MemoryV2Config) -> None:
        self.config = config

    def connect(self) -> sqlite3.Connection:
        """Open the derived SQLite store."""
        ensure_parent(self.config.db_path)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        return conn

    def status(self) -> dict[str, Any]:
        """Return index status and compatibility diagnostics."""
        with self.connect() as conn:
            docs = conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
            chunks = conn.execute("SELECT COUNT(*) AS count FROM search_items").fetchone()
            dirty = self.is_dirty(conn=conn)
        return {
            "backend": "strongclaw-v2",
            "provider": "strongclaw-memory-v2",
            "model": "sqlite-fts5",
            "workspaceDir": self.config.workspace_root.as_posix(),
            "dbPath": self.config.db_path.as_posix(),
            "files": int(docs["count"]) if docs is not None else 0,
            "chunks": int(chunks["count"]) if chunks is not None else 0,
            "dirty": dirty,
            "markdownCanonical": True,
            "memoryFiles": list(self.config.memory_file_names),
        }

    def is_dirty(self, *, conn: sqlite3.Connection | None = None) -> bool:
        """Return True when the derived index no longer matches source Markdown."""
        owns_conn = conn is None
        if conn is None:
            conn = self.connect()
        try:
            current = {doc.rel_path: doc.sha256 for doc in self._iter_documents()}
            existing = {
                str(row["rel_path"]): str(row["sha256"])
                for row in conn.execute("SELECT rel_path, sha256 FROM documents")
            }
            return current != existing
        finally:
            if owns_conn:
                conn.close()

    def reindex(self) -> ReindexSummary:
        """Rebuild the derived index from canonical Markdown files."""
        documents = list(self._iter_documents())
        with self.connect() as conn:
            existing = {
                str(row["rel_path"]): str(row["sha256"])
                for row in conn.execute("SELECT rel_path, sha256 FROM documents")
            }
            current = {doc.rel_path: doc.sha256 for doc in documents}
            dirty = current != existing
            conn.execute("DELETE FROM search_items_fts")
            conn.execute("DELETE FROM search_items")
            conn.execute("DELETE FROM documents")
            indexed_at = datetime.now(tz=UTC).isoformat()
            chunks = 0
            for document in documents:
                doc_cursor = conn.execute(
                    """
                    INSERT INTO documents (rel_path, abs_path, lane, source_name, sha256, line_count, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.rel_path,
                        document.abs_path.as_posix(),
                        document.lane,
                        document.source_name,
                        document.sha256,
                        document.line_count,
                        indexed_at,
                    ),
                )
                document_id = doc_cursor.lastrowid
                if document_id is None:
                    raise RuntimeError("document insert did not return a rowid")
                for item in document.items:
                    item_cursor = conn.execute(
                        """
                        INSERT INTO search_items (
                            document_id,
                            rel_path,
                            lane,
                            source_name,
                            item_type,
                            title,
                            snippet,
                            start_line,
                            end_line,
                            confidence,
                            entities_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            document_id,
                            document.rel_path,
                            document.lane,
                            document.source_name,
                            item.item_type,
                            item.title,
                            item.snippet,
                            item.start_line,
                            item.end_line,
                            item.confidence,
                            json.dumps(list(item.entities), sort_keys=True),
                        ),
                    )
                    item_row_id = item_cursor.lastrowid
                    if item_row_id is None:
                        raise RuntimeError("search item insert did not return a rowid")
                    conn.execute(
                        "INSERT INTO search_items_fts(rowid, title, snippet, entities) VALUES (?, ?, ?, ?)",
                        (
                            item_row_id,
                            item.title,
                            item.snippet,
                            " ".join(item.entities),
                        ),
                    )
                    chunks += 1
            conn.commit()
        return ReindexSummary(files=len(documents), chunks=chunks, dirty=dirty)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        min_score: float | None = None,
        lane: Lane | Literal["all"] = "all",
        auto_index: bool = True,
    ) -> list[SearchHit]:
        """Search the derived store with FTS first and substring fallback second."""
        if auto_index and self.is_dirty():
            self.reindex()
        limit = max_results if max_results is not None else self.config.default_max_results
        if limit <= 0:
            raise ValueError("max_results must be positive")
        terms = self._normalize_query(query)
        if not terms:
            return []
        lane_clause = ""
        params: list[Any] = []
        if lane != "all":
            lane_clause = "AND si.lane = ?"
            params.append(lane)
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    si.rel_path,
                    si.start_line,
                    si.end_line,
                    si.snippet,
                    si.lane,
                    si.item_type,
                    si.confidence,
                    si.entities_json,
                    bm25(search_items_fts) AS rank
                FROM search_items_fts
                JOIN search_items AS si ON si.id = search_items_fts.rowid
                WHERE search_items_fts MATCH ?
                {lane_clause}
                ORDER BY rank ASC
                LIMIT ?
                """,
                [fts_query, *params, limit],
            ).fetchall()
            hits = [self._row_to_hit(row) for row in rows]
            if not hits:
                substring_rows = conn.execute(
                    f"""
                    SELECT
                        rel_path,
                        start_line,
                        end_line,
                        snippet,
                        lane,
                        item_type,
                        confidence,
                        entities_json
                    FROM search_items
                    WHERE lower(title || ' ' || snippet) LIKE ?
                    {lane_clause.replace('si.', '')}
                    ORDER BY length(snippet) ASC, rel_path ASC, start_line ASC
                    LIMIT ?
                    """,
                    [f"%{' '.join(terms).lower()}%", *params, limit],
                ).fetchall()
                hits = [self._row_to_hit(row, default_score=0.25) for row in substring_rows]
        filtered = [hit for hit in hits if min_score is None or hit.score >= min_score]
        return filtered[:limit]

    def read(
        self, rel_path: str, *, from_line: int | None = None, lines: int | None = None
    ) -> dict[str, Any]:
        """Read a specific file or line range from canonical storage."""
        target = self._resolve_read_path(rel_path)
        if target is None or not target.exists():
            return {"path": rel_path, "text": ""}
        content_lines = target.read_text(encoding="utf-8").splitlines()
        start_index = max(0, (from_line - 1) if from_line else 0)
        end_index = len(content_lines) if lines is None else max(start_index, start_index + lines)
        selected = content_lines[start_index:end_index]
        text = "\n".join(selected)
        if selected:
            text += "\n"
        return {"path": rel_path, "text": text}

    def store(
        self,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        text: str,
        entity: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Append a durable memory entry to the appropriate canonical Markdown file."""
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")
        target = self._store_target(kind=kind, entity=entity)
        entry_line = self._format_entry_line(
            kind=kind, text=text, entity=entity, confidence=confidence
        )
        changed = self._append_unique_entry(target, kind=kind, entry_line=entry_line)
        summary = self.reindex()
        return {
            "ok": True,
            "stored": changed,
            "path": _resolve_under_workspace(self.config.workspace_root, target),
            "entry": entry_line,
            "index": summary.to_dict(),
        }

    def update(
        self,
        *,
        rel_path: str,
        find_text: str,
        replace_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Replace text inside a writable canonical Markdown file and reindex."""
        if not find_text:
            raise ValueError("find_text must not be empty")
        target = self._resolve_writable_path(rel_path)
        original = target.read_text(encoding="utf-8")
        if find_text not in original:
            raise ValueError(f"{find_text!r} not found in {rel_path}")
        count = original.count(find_text) if replace_all else 1
        updated = original.replace(find_text, replace_text, -1 if replace_all else 1)
        write_text(target, updated)
        summary = self.reindex()
        return {
            "ok": True,
            "path": rel_path,
            "replacements": count,
            "index": summary.to_dict(),
        }

    def reflect(self) -> dict[str, Any]:
        """Promote retained daily-log entries into durable bank pages."""
        reflected = {"fact": 0, "reflection": 0, "opinion": 0, "entity": 0}
        daily_dir = self.config.workspace_root / self.config.daily_dir
        for path in sorted(daily_dir.glob("*.md")):
            for note in self._iter_retained_notes(path):
                if self._append_unique_entry(
                    self._store_target(kind=note["kind"], entity=note.get("entity")),
                    kind=note["kind"],
                    entry_line=note["entry"],
                ):
                    reflected[note["kind"]] += 1
        summary = self.reindex()
        return {"ok": True, "reflected": reflected, "index": summary.to_dict()}

    def _iter_documents(self) -> Iterator[IndexedDocument]:
        """Yield all documents that belong in the derived index."""
        seen: set[str] = set()
        if self.config.include_default_memory:
            for document in self._iter_memory_documents():
                if document.rel_path not in seen:
                    seen.add(document.rel_path)
                    yield document
        for corpus_entry in self.config.corpus_paths:
            for document in self._iter_corpus_documents(corpus_entry):
                if document.rel_path not in seen:
                    seen.add(document.rel_path)
                    yield document

    def _iter_memory_documents(self) -> Iterator[IndexedDocument]:
        """Yield workspace memory and bank documents."""
        for name in self.config.memory_file_names:
            path = self.config.workspace_root / name
            if path.exists() and path.is_file():
                yield self._document_from_path(path, lane="memory", source_name="memory-root")
                break
        for root_name, source_name in (
            (self.config.daily_dir, "daily"),
            (self.config.bank_dir, "bank"),
        ):
            root = self.config.workspace_root / root_name
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                if path.is_file():
                    yield self._document_from_path(path, lane="memory", source_name=source_name)

    def _iter_corpus_documents(self, entry: CorpusPathConfig) -> Iterator[IndexedDocument]:
        """Yield configured corpus Markdown files."""
        if entry.path.is_file():
            if _matches_glob(entry.path.name, entry.pattern):
                yield self._document_from_path(entry.path, lane="corpus", source_name=entry.name)
            return
        if not entry.path.exists():
            return
        for path in sorted(entry.path.rglob("*.md")):
            rel_name = path.relative_to(entry.path).as_posix()
            if _matches_glob(rel_name, entry.pattern):
                yield self._document_from_path(path, lane="corpus", source_name=entry.name)

    def _document_from_path(
        self, path: pathlib.Path, *, lane: Lane, source_name: str
    ) -> IndexedDocument:
        """Parse a Markdown file into a document plus indexed items."""
        content = path.read_text(encoding="utf-8")
        rel_path = _resolve_under_workspace(self.config.workspace_root, path)
        lines = content.splitlines()
        parsed_items = tuple(self._parse_markdown(lines))
        return IndexedDocument(
            rel_path=rel_path,
            abs_path=path.resolve(),
            lane=lane,
            source_name=source_name,
            sha256=self._sha256(content),
            line_count=len(lines),
            items=parsed_items,
        )

    def _parse_markdown(self, lines: Sequence[str]) -> Iterator[ParsedItem]:
        """Split Markdown into searchable paragraphs and typed entries."""
        headings: list[str] = []
        paragraph_lines: list[str] = []
        paragraph_start = 1

        def flush_paragraph(end_line: int) -> Iterator[ParsedItem]:
            if not paragraph_lines:
                return
            snippet = " ".join(line.strip() for line in paragraph_lines if line.strip()).strip()
            if snippet:
                title = " / ".join(headings) if headings else "Document"
                yield self._build_item(
                    item_type="paragraph",
                    title=title,
                    snippet=snippet,
                    start_line=paragraph_start,
                    end_line=end_line,
                )
            paragraph_lines.clear()

        for line_number, raw_line in enumerate(lines, start=1):
            heading_match = HEADING_RE.match(raw_line)
            if heading_match:
                yield from flush_paragraph(line_number - 1)
                level = len(heading_match.group("level"))
                title = heading_match.group("title").strip()
                headings[:] = headings[: level - 1]
                headings.append(title)
                yield self._build_item(
                    item_type="section",
                    title=" / ".join(headings),
                    snippet=title,
                    start_line=line_number,
                    end_line=line_number,
                )
                continue
            bullet_match = BULLET_RE.match(raw_line)
            if bullet_match:
                yield from flush_paragraph(line_number - 1)
                body = bullet_match.group("body").strip()
                typed = self._parse_typed_entry(body)
                title = " / ".join(headings) if headings else "Document"
                if typed is not None:
                    yield self._build_item(
                        item_type=typed["kind"],
                        title=title,
                        snippet=typed["entry"],
                        start_line=line_number,
                        end_line=line_number,
                        confidence=typed.get("confidence"),
                        entities=typed.get("entities", ()),
                    )
                else:
                    yield self._build_item(
                        item_type="paragraph",
                        title=title,
                        snippet=body,
                        start_line=line_number,
                        end_line=line_number,
                    )
                continue
            if not raw_line.strip():
                yield from flush_paragraph(line_number - 1)
                paragraph_start = line_number + 1
                continue
            if not paragraph_lines:
                paragraph_start = line_number
            paragraph_lines.append(raw_line)
        yield from flush_paragraph(len(lines))

    def _build_item(
        self,
        *,
        item_type: EntryType,
        title: str,
        snippet: str,
        start_line: int,
        end_line: int,
        confidence: float | None = None,
        entities: Iterable[str] = (),
    ) -> ParsedItem:
        """Construct an indexed item with truncation and entity extraction."""
        normalized = snippet.strip()
        clipped = normalized[: self.config.max_snippet_chars].rstrip()
        merged_entities = set(entities)
        merged_entities.update(ENTITY_TAG_RE.findall(f"{title} {normalized}"))
        return ParsedItem(
            item_type=item_type,
            title=title,
            snippet=clipped,
            start_line=start_line,
            end_line=end_line,
            confidence=confidence,
            entities=tuple(sorted(merged_entities)),
        )

    def _row_to_hit(self, row: sqlite3.Row, *, default_score: float | None = None) -> SearchHit:
        """Convert a search row into the public hit shape."""
        rank = row["rank"] if "rank" in row.keys() else None
        score = default_score if default_score is not None else self._rank_to_score(rank)
        return SearchHit(
            path=str(row["rel_path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            score=score,
            snippet=str(row["snippet"]),
            lane="memory" if str(row["lane"]) == "memory" else "corpus",
            item_type=str(row["item_type"]),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            entities=tuple(json.loads(str(row["entities_json"]))),
        )

    def _rank_to_score(self, rank: object) -> float:
        """Map FTS ranks to a higher-is-better score."""
        if not isinstance(rank, (int, float)):
            return 0.5
        return 1.0 / (1.0 + abs(float(rank)))

    def _normalize_query(self, query: str) -> list[str]:
        """Extract search terms from a free-form query string."""
        return re.findall(r"[A-Za-z0-9_]{2,}", query.lower())

    def _resolve_read_path(self, rel_path: str) -> pathlib.Path | None:
        """Resolve a relative path within the workspace root."""
        normalized = pathlib.PurePosixPath(rel_path)
        if normalized.is_absolute() or ".." in normalized.parts:
            return None
        target = (self.config.workspace_root / normalized.as_posix()).resolve()
        try:
            target.relative_to(self.config.workspace_root)
        except ValueError:
            return None
        return target

    def _resolve_writable_path(self, rel_path: str) -> pathlib.Path:
        """Resolve a writable canonical path."""
        if rel_path not in self.config.memory_file_names and not rel_path.startswith(
            WRITABLE_PREFIXES
        ):
            raise ValueError(f"{rel_path} is not a writable canonical memory path")
        target = self._resolve_read_path(rel_path)
        if target is None:
            raise ValueError(f"{rel_path} is not a valid workspace-relative path")
        ensure_parent(target)
        return target

    def _store_target(
        self,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        entity: str | None = None,
    ) -> pathlib.Path:
        """Choose the target Markdown file for a durable entry."""
        bank_root = self.config.workspace_root / self.config.bank_dir
        if kind == "fact":
            return bank_root / "world.md"
        if kind == "reflection":
            return bank_root / "experience.md"
        if kind == "opinion":
            return bank_root / "opinions.md"
        if kind == "entity":
            if not entity:
                raise ValueError("entity name is required for entity entries")
            return bank_root / "entities" / f"{self._slugify(entity)}.md"
        raise ValueError(f"unsupported memory type: {kind}")

    def _format_entry_line(
        self,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        text: str,
        entity: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Format a durable Markdown bullet line."""
        if kind == "entity":
            if not entity:
                raise ValueError("entity name is required for entity entries")
            return f"- Entity[{entity}]: {text}"
        if kind == "opinion":
            if confidence is not None:
                return f"- Opinion[c={confidence:.2f}]: {text}"
            return f"- Opinion: {text}"
        return f"- {ENTRY_LABELS[kind]}: {text}"

    def _append_unique_entry(
        self,
        path: pathlib.Path,
        *,
        kind: Literal["fact", "reflection", "opinion", "entity"],
        entry_line: str,
    ) -> bool:
        """Append *entry_line* once under a stable `## Entries` section."""
        header = self._document_header(path=path, kind=kind)
        if not path.exists():
            write_text(path, header + entry_line + "\n")
            return True
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if entry_line in lines:
            return False
        if "## Entries" not in text:
            suffix = "" if text.endswith("\n") or not text else "\n"
            write_text(path, text + suffix + "\n## Entries\n" + entry_line + "\n")
            return True
        anchor = text.index("## Entries")
        insertion = text.find("\n", anchor)
        if insertion == -1:
            insertion = len(text)
            prefix = text + "\n"
        else:
            prefix = text[: insertion + 1]
        remainder = text[insertion + 1 :]
        write_text(path, prefix + entry_line + "\n" + remainder.lstrip("\n"))
        return True

    def _document_header(
        self,
        *,
        path: pathlib.Path,
        kind: Literal["fact", "reflection", "opinion", "entity"],
    ) -> str:
        """Return the initial file header for a target bank document."""
        if kind == "entity":
            entity_name = path.stem.replace("-", " ").title()
            return f"# Entity: {entity_name}\n\n## Entries\n"
        return BANK_HEADERS[kind]

    def _iter_retained_notes(
        self,
        path: pathlib.Path,
    ) -> Iterator[dict[str, Any]]:
        """Yield typed retain bullets from a daily log file."""
        lines = path.read_text(encoding="utf-8").splitlines()
        in_retain = False
        for raw_line in lines:
            if RETAIN_HEADER_RE.match(raw_line.strip()):
                in_retain = True
                continue
            if in_retain and raw_line.startswith("#"):
                in_retain = False
                continue
            if not in_retain:
                continue
            bullet_match = BULLET_RE.match(raw_line)
            if not bullet_match:
                continue
            parsed = self._parse_typed_entry(bullet_match.group("body").strip())
            if parsed is None:
                entry = self._format_entry_line(
                    kind="fact", text=bullet_match.group("body").strip()
                )
                yield {"kind": "fact", "entry": entry}
                continue
            yield parsed

    def _parse_typed_entry(self, text: str) -> dict[str, Any] | None:
        """Parse a typed memory bullet line."""
        match = TYPED_ENTRY_RE.match(text)
        if not match:
            return None
        raw_kind = match.group("kind").lower()
        entry_text = match.group("text").strip()
        entity = match.group("entity")
        confidence = match.group("confidence")
        if raw_kind.startswith("opinion"):
            kind: Literal["fact", "reflection", "opinion", "entity"] = "opinion"
            line = self._format_entry_line(
                kind="opinion",
                text=entry_text,
                confidence=float(confidence) if confidence is not None else None,
            )
        elif raw_kind.startswith("reflection"):
            kind = "reflection"
            line = self._format_entry_line(kind="reflection", text=entry_text)
        elif raw_kind.startswith("entity"):
            kind = "entity"
            line = self._format_entry_line(kind="entity", text=entry_text, entity=entity)
        else:
            kind = "fact"
            line = self._format_entry_line(kind="fact", text=entry_text)
        entities = tuple(sorted(set(ENTITY_TAG_RE.findall(line)) | ({entity} if entity else set())))
        return {
            "kind": kind,
            "entry": line,
            "confidence": float(confidence) if confidence is not None else None,
            "entity": entity,
            "entities": entities,
        }

    def _slugify(self, text: str) -> str:
        """Create a filesystem-safe slug."""
        slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
        if not slug:
            raise ValueError("entity name must contain alphanumeric characters")
        return slug

    def _sha256(self, content: str) -> str:
        """Return the SHA-256 hex digest of *content*."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the memory-v2 engine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=default_config_path(),
        help="Path to the memory-v2 YAML config.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show index status.")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    index_parser = subparsers.add_parser("index", help="Rebuild the derived index.")
    index_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    search_parser = subparsers.add_parser("search", help="Search the derived memory index.")
    search_parser.add_argument("query", nargs="?", help="Search query.")
    search_parser.add_argument("--query", dest="query_flag", help="Search query.")
    search_parser.add_argument("--max-results", type=int, help="Maximum results.")
    search_parser.add_argument("--min-score", type=float, help="Minimum score threshold.")
    search_parser.add_argument("--lane", choices=("all", "memory", "corpus"), default="all")
    search_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    get_parser = subparsers.add_parser("get", help="Read a canonical memory or corpus file.")
    get_parser.add_argument("path", help="Workspace-relative path returned by search.")
    get_parser.add_argument("--from", dest="from_line", type=int, help="1-based start line.")
    get_parser.add_argument("--lines", type=int, help="Number of lines to read.")
    get_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    store_parser = subparsers.add_parser("store", help="Append a durable memory entry.")
    store_parser.add_argument(
        "--type", choices=("fact", "reflection", "opinion", "entity"), required=True
    )
    store_parser.add_argument("--text", required=True)
    store_parser.add_argument("--entity")
    store_parser.add_argument("--confidence", type=float)
    store_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    update_parser = subparsers.add_parser(
        "update", help="Replace text inside a writable memory file."
    )
    update_parser.add_argument("--path", required=True)
    update_parser.add_argument("--find", dest="find_text", required=True)
    update_parser.add_argument("--replace", dest="replace_text", required=True)
    update_parser.add_argument("--all", action="store_true", help="Replace all occurrences.")
    update_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    reflect_parser = subparsers.add_parser(
        "reflect", help="Promote retained notes into bank pages."
    )
    reflect_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    return parser.parse_args(argv)


def _print_payload(payload: Mapping[str, Any], *, as_json: bool) -> None:
    """Print a result payload."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    """Run the memory-v2 CLI."""
    args = parse_args(argv)
    config = load_config(args.config)
    engine = MemoryV2Engine(config)
    if args.command == "status":
        _print_payload(engine.status(), as_json=bool(args.json))
        return 0
    if args.command == "index":
        _print_payload(engine.reindex().to_dict(), as_json=bool(args.json))
        return 0
    if args.command == "search":
        query = (args.query_flag or args.query or "").strip()
        if not query:
            raise SystemExit("search query required")
        hits = engine.search(
            query,
            max_results=args.max_results,
            min_score=args.min_score,
            lane=args.lane,
        )
        payload = {
            "results": [hit.to_dict() for hit in hits],
            "provider": "strongclaw-memory-v2",
            "model": "sqlite-fts5",
            "mode": args.lane,
        }
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "get":
        payload = engine.read(args.path, from_line=args.from_line, lines=args.lines)
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "store":
        payload = engine.store(
            kind=args.type,
            text=args.text,
            entity=args.entity,
            confidence=args.confidence,
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "update":
        payload = engine.update(
            rel_path=args.path,
            find_text=args.find_text,
            replace_text=args.replace_text,
            replace_all=bool(args.all),
        )
        _print_payload(payload, as_json=bool(args.json))
        return 0
    if args.command == "reflect":
        payload = engine.reflect()
        _print_payload(payload, as_json=bool(args.json))
        return 0
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
