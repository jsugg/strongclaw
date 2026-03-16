"""Repository indexing and lexical context packing service."""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sqlite3
import subprocess
from typing import Any, Iterable

from clawops.common import expand, load_yaml, sha256_hex, utc_now_ms, write_text


DEFAULT_TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".sh", ".bash", ".zsh", ".sql",
    ".go", ".rs", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp",
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    language TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
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


@dataclasses.dataclass(slots=True)
class SearchHit:
    """Search result."""

    path: str
    snippet: str
    symbols: list[str]


class ContextService:
    """Lexical repo indexer and query engine."""

    def __init__(self, repo: pathlib.Path, db_path: pathlib.Path) -> None:
        self.repo = repo.expanduser().resolve()
        self.db_path = db_path.expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        """Open the sqlite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def iter_files(self, include_hidden: bool = False) -> Iterable[pathlib.Path]:
        """Yield indexable files from the repository."""
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            if not include_hidden and any(part.startswith(".") for part in path.relative_to(self.repo).parts):
                continue
            if path.suffix.lower() not in DEFAULT_TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            yield path

    def index(self) -> int:
        """Index repository contents into the lexical store."""
        count = 0
        with self.connect() as conn:
            for path in self.iter_files():
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                rel = str(path.relative_to(self.repo))
                sha = sha256_hex(text)
                symbols = extract_symbols(path, text)
                mtime_ns = path.stat().st_mtime_ns
                conn.execute(
                    """
                    INSERT INTO files(path, sha256, language, mtime_ns, symbols, content)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        sha256 = excluded.sha256,
                        language = excluded.language,
                        mtime_ns = excluded.mtime_ns,
                        symbols = excluded.symbols,
                        content = excluded.content
                    """,
                    (rel, sha, detect_language(path), mtime_ns, "\n".join(symbols), text),
                )
                conn.execute("DELETE FROM files_fts WHERE path = ?", (rel,))
                conn.execute(
                    "INSERT INTO files_fts(path, content, symbols) VALUES (?, ?, ?)",
                    (rel, text, "\n".join(symbols)),
                )
                count += 1
            conn.commit()
        return count

    def query(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        """Run a lexical query.

        SQLite FTS tokenization can miss identifiers with underscores or language-
        specific punctuation, so the implementation falls back to a substring
        scan when the FTS query returns no rows.
        """
        with self.connect() as conn:
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
            content = str(row["content"])
            snippet = content[:1200]
            symbols = [s for s in str(row["symbols"]).splitlines() if s]
            hits.append(SearchHit(path=str(row["path"]), snippet=snippet, symbols=symbols))
        return hits

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
            lines.append("```text")
            lines.append(hit.snippet)
            lines.append("```")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def load_config(path: pathlib.Path) -> dict[str, Any]:
    """Load the context-service YAML config."""
    config = load_yaml(path)
    if not isinstance(config, dict):
        raise TypeError(f"expected mapping config, got: {type(config)!r}")
    return config


def service_from_config(config_path: pathlib.Path, repo: pathlib.Path) -> ContextService:
    """Build a ContextService from a YAML config."""
    config = load_config(config_path)
    db_path = pathlib.Path(config.get("index", {}).get("db_path", ".clawops/context.sqlite"))
    if not db_path.is_absolute():
        db_path = repo / db_path
    return ContextService(repo, db_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse context CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("index", "query", "pack"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True, type=pathlib.Path)
        cmd.add_argument("--repo", required=True, type=pathlib.Path)

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
        count = service.index()
        print(f"indexed={count}")
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
