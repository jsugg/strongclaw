"""Unit tests for hypermemory/services/index_service.py."""

from __future__ import annotations

import sqlite3

from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.services.index_service import IndexService
from clawops.hypermemory.sparse import build_sparse_encoder


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_service(conn: sqlite3.Connection) -> IndexService:
    return IndexService(connect=lambda: conn)


def test_count_rows_empty_table() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    assert svc.count_rows(conn, "documents") == 0


def test_count_rows_after_insert() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    conn.execute(
        "INSERT INTO documents (rel_path, abs_path, lane, source_name, sha256, line_count,"
        " modified_at, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
        ("MEMORY.md", "/ws/MEMORY.md", "memory", "main", "abc", 3, "2026-01-01", "2026-01-01"),
    )
    conn.commit()
    assert svc.count_rows(conn, "documents") == 1


def test_count_sparse_vector_items_empty() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    assert svc.count_sparse_vector_items(conn) == 0


def _insert_search_item_for_vector(conn: sqlite3.Connection, rel_path: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rel_path, f"/ws/{rel_path}", "memory", "main", "abc", 3, "2026-01-01", "2026-01-01"),
    )
    doc_id = conn.execute("SELECT id FROM documents WHERE rel_path = ?", (rel_path,)).fetchone()[
        "id"
    ]
    conn.execute(
        "INSERT INTO search_items "
        "(document_id, rel_path, lane, source_name, source_kind, item_type, title, snippet, "
        "normalized_text, start_line, end_line, scope, modified_at, "
        "contradiction_count, evidence_count, entities_json, evidence_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            rel_path,
            "memory",
            "main",
            "markdown",
            "fact",
            "t",
            "s",
            "s",
            1,
            1,
            "global",
            "2026-01-01",
            0,
            0,
            "[]",
            "[]",
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def test_count_sparse_vector_items_filters_non_sparse() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    # Insert two search_items (parent FK rows) then two vector_items
    id1 = _insert_search_item_for_vector(conn, "a.md")
    id2 = _insert_search_item_for_vector(conn, "b.md")
    conn.execute(
        "INSERT INTO vector_items"
        " (item_id, point_id, embedding_model, embedding_dim, content_sha256,"
        " sparse_term_count, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (id1, "pt-1", "all-MiniLM-L6-v2", 384, "abc123", 5, "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO vector_items"
        " (item_id, point_id, embedding_model, embedding_dim, content_sha256,"
        " sparse_term_count, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (id2, "pt-2", "all-MiniLM-L6-v2", 384, "def456", 0, "2026-01-01"),
    )
    conn.commit()
    assert svc.count_sparse_vector_items(conn) == 1


def test_backend_state_value_missing_key_returns_none() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    assert svc.backend_state_value(conn, "nonexistent_key") is None


def test_backend_state_value_after_write() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    svc.write_backend_state(conn, "config_fingerprint", "abc123")
    conn.commit()
    assert svc.backend_state_value(conn, "config_fingerprint") == "abc123"


def test_write_backend_state_insert_or_replace() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    svc.write_backend_state(conn, "last_sync_at", "2026-01-01T00:00:00Z")
    svc.write_backend_state(conn, "last_sync_at", "2026-02-01T00:00:00Z")
    conn.commit()
    assert svc.backend_state_value(conn, "last_sync_at") == "2026-02-01T00:00:00Z"


def test_write_backend_state_multiple_keys() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    svc.write_backend_state(conn, "key_a", "value_a")
    svc.write_backend_state(conn, "key_b", "value_b")
    conn.commit()
    assert svc.backend_state_value(conn, "key_a") == "value_a"
    assert svc.backend_state_value(conn, "key_b") == "value_b"


def test_write_sparse_state_disabled_clears_keys() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    encoder = build_sparse_encoder(["deploy token rotate"])
    svc.write_sparse_state(conn, encoder, enabled=False)
    conn.commit()
    assert svc.backend_state_value(conn, "sparse_fingerprint") == ""
    assert svc.backend_state_value(conn, "sparse_doc_count") == "0"
    assert svc.backend_state_value(conn, "sparse_avg_doc_length") == "0"


def test_write_sparse_state_disabled_does_not_populate_terms_table() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    encoder = build_sparse_encoder(["gateway token rotate"])
    svc.write_sparse_state(conn, encoder, enabled=False)
    conn.commit()
    assert svc.count_rows(conn, "sparse_terms") == 0


def test_write_sparse_state_enabled_populates_terms() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    encoder = build_sparse_encoder(["gateway token", "deploy token rotate"])
    svc.write_sparse_state(conn, encoder, enabled=True)
    conn.commit()
    term_count = svc.count_rows(conn, "sparse_terms")
    assert term_count == len(encoder.term_to_id)
    assert term_count > 0


def test_write_sparse_state_enabled_writes_metadata() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    encoder = build_sparse_encoder(["gateway token rotate"])
    svc.write_sparse_state(conn, encoder, enabled=True)
    conn.commit()
    assert svc.backend_state_value(conn, "sparse_fingerprint") == encoder.fingerprint
    assert svc.backend_state_value(conn, "sparse_doc_count") == str(encoder.document_count)


def test_load_sparse_encoder_disabled_returns_none() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    assert svc.load_sparse_encoder(conn, enabled=False) is None


def test_load_sparse_encoder_empty_store_returns_none() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    assert svc.load_sparse_encoder(conn, enabled=True) is None


def test_load_sparse_encoder_round_trip() -> None:
    conn = _make_conn()
    svc = _make_service(conn)
    original = build_sparse_encoder(["gateway token rotate", "deploy checklist"])
    svc.write_sparse_state(conn, original, enabled=True)
    conn.commit()

    loaded = svc.load_sparse_encoder(conn, enabled=True)
    assert loaded is not None
    assert loaded.fingerprint == original.fingerprint
    assert loaded.document_count == original.document_count
    assert set(loaded.term_to_id.keys()) == set(original.term_to_id.keys())
