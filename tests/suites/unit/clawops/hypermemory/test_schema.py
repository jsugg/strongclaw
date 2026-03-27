"""Tests for packaged hypermemory schema loading."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from clawops.hypermemory.schema import SCHEMA_VERSION, ensure_schema, schema_definition


def test_hypermemory_schema_resource_is_versioned() -> None:
    definition = schema_definition()

    assert definition.version == SCHEMA_VERSION
    assert "CREATE TABLE IF NOT EXISTS search_items" in definition.script
    assert "DROP TABLE IF EXISTS search_items" in definition.drop_statements


def test_hypermemory_schema_bootstraps_empty_database(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "hypermemory.sqlite")

    ensure_schema(conn)

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }
    version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()

    assert "documents" in tables
    assert "search_items" in tables
    assert "vector_items" in tables
    assert version == (SCHEMA_VERSION,)


def test_hypermemory_schema_rebuilds_when_version_drifts(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "hypermemory.sqlite")
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO documents (
            rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("MEMORY.md", "/tmp/MEMORY.md", "memory", "memory", "abc", 1, "2026-03-24", "2026-03-24"),
    )
    conn.execute("UPDATE meta SET value = 'legacy' WHERE key = 'schema_version'")
    conn.commit()

    ensure_schema(conn)

    document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()

    assert document_count == (0,)
    assert version == (SCHEMA_VERSION,)
