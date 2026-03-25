"""SQLite schema management for StrongClaw hypermemory."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

_SCHEMA_RESOURCE = "resources/schema.sql"
_SCHEMA_VERSION_RE = re.compile(r"^--\s*schema_version:\s*(?P<version>\S+)\s*$", re.MULTILINE)
_TABLE_RE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SchemaDefinition:
    """Packaged hypermemory schema definition."""

    version: str
    script: str
    drop_statements: tuple[str, ...]


def _load_schema_text() -> str:
    resource = files("clawops.hypermemory").joinpath(_SCHEMA_RESOURCE)
    return resource.read_text(encoding="utf-8")


def _schema_version(script: str) -> str:
    match = _SCHEMA_VERSION_RE.search(script)
    if match is None:
        raise ValueError("hypermemory schema resource must declare a schema_version comment")
    return match.group("version")


def _drop_statements(script: str) -> tuple[str, ...]:
    names = [
        match.group("name") for match in _TABLE_RE.finditer(script) if match.group("name") != "meta"
    ]
    return tuple(f"DROP TABLE IF EXISTS {name}" for name in reversed(names))


@lru_cache(maxsize=1)
def schema_definition() -> SchemaDefinition:
    """Return the cached packaged schema definition."""
    script = _load_schema_text()
    return SchemaDefinition(
        version=_schema_version(script),
        script=script,
        drop_statements=_drop_statements(script),
    )


SCHEMA_VERSION = schema_definition().version


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the current schema version exists, recreating derived tables if needed."""
    definition = schema_definition()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or str(row[0]) != definition.version:
        for statement in definition.drop_statements:
            conn.execute(statement)
        conn.executescript(definition.script)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (definition.version,),
        )
        conn.commit()
