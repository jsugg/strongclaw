"""SQLite schema management for strongclaw memory v2."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "4.0"

DROP_STATEMENTS = (
    "DROP TABLE IF EXISTS backend_state",
    "DROP TABLE IF EXISTS vector_items",
    "DROP TABLE IF EXISTS sparse_terms",
    "DROP TABLE IF EXISTS conflicts",
    "DROP TABLE IF EXISTS evidence_links",
    "DROP TABLE IF EXISTS proposals",
    "DROP TABLE IF EXISTS facts",
    "DROP TABLE IF EXISTS opinions",
    "DROP TABLE IF EXISTS reflections",
    "DROP TABLE IF EXISTS entities",
    "DROP TABLE IF EXISTS search_items_fts",
    "DROP TABLE IF EXISTS search_items",
    "DROP TABLE IF EXISTS documents",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        rel_path TEXT NOT NULL UNIQUE,
        abs_path TEXT NOT NULL,
        lane TEXT NOT NULL,
        source_name TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        line_count INTEGER NOT NULL,
        modified_at TEXT NOT NULL,
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
        source_kind TEXT NOT NULL,
        item_type TEXT NOT NULL,
        title TEXT NOT NULL,
        snippet TEXT NOT NULL,
        normalized_text TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        confidence REAL,
        scope TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        contradiction_count INTEGER NOT NULL DEFAULT 0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        entities_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL
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
    """
    CREATE TABLE IF NOT EXISTS sparse_terms (
        term TEXT PRIMARY KEY,
        term_id INTEGER NOT NULL UNIQUE,
        document_freq INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vector_items (
        item_id INTEGER PRIMARY KEY REFERENCES search_items(id) ON DELETE CASCADE,
        point_id TEXT NOT NULL UNIQUE,
        embedding_model TEXT NOT NULL,
        embedding_dim INTEGER NOT NULL,
        content_sha256 TEXT NOT NULL,
        sparse_term_count INTEGER NOT NULL DEFAULT 0,
        sparse_content_sha256 TEXT NOT NULL DEFAULT '',
        sparse_updated_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backend_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS facts (
        item_id INTEGER PRIMARY KEY REFERENCES search_items(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT NOT NULL,
        text TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS opinions (
        item_id INTEGER PRIMARY KEY REFERENCES search_items(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT NOT NULL,
        text TEXT NOT NULL,
        confidence REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reflections (
        item_id INTEGER PRIMARY KEY REFERENCES search_items(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT NOT NULL,
        text TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
        item_id INTEGER PRIMARY KEY REFERENCES search_items(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT NOT NULL,
        name TEXT NOT NULL,
        text TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_links (
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES search_items(id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        relation TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conflicts (
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES search_items(id) ON DELETE CASCADE,
        target_ref TEXT NOT NULL,
        reason TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS proposals (
        proposal_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL,
        entry_line TEXT NOT NULL,
        source_rel_path TEXT NOT NULL,
        source_line INTEGER NOT NULL,
        target_rel_path TEXT NOT NULL,
        entity TEXT,
        confidence REAL,
        created_at TEXT NOT NULL
    )
    """,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the current schema version exists, recreating derived tables if needed."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(SCHEMA_STATEMENTS[0])
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or str(row[0]) != SCHEMA_VERSION:
        for statement in DROP_STATEMENTS:
            conn.execute(statement)
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
