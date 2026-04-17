"""Unit tests for hypermemory/search_hit_mapper.py."""

from __future__ import annotations

import sqlite3

from clawops.hypermemory.models import Tier
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.search_hit_mapper import row_to_search_hit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTierNormalizer:
    """Minimal _TierNormalizer protocol implementation for tests."""

    def normalize_tier(self, value: str) -> Tier:
        if value in ("core", "working", "peripheral"):
            return value  # type: ignore[return-value]
        return "working"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_search_item(
    conn: sqlite3.Connection,
    *,
    rel_path: str = "bank/world.md",
    lane: str = "memory",
    item_type: str = "fact",
    snippet: str = "The deploy uses blue/green.",
    start_line: int = 1,
    end_line: int = 1,
    scope: str = "global",
    confidence: float | None = 0.9,
    importance: float | None = 0.8,
    tier: str = "working",
    entities_json: str = '["Alice"]',
    access_count: int = 3,
    last_access_date: str | None = "2026-03-24",
    injected_count: int = 2,
    confirmed_count: int = 1,
    bad_recall_count: int = 0,
    fact_key: str | None = "user:name",
    invalidated_at: str | None = None,
    supersedes: str | None = None,
) -> int:
    # First insert a document row (required FK)
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rel_path, f"/workspace/{rel_path}", lane, "test", "abc", 10, "2026-03-24", "2026-03-24"),
    )
    doc_id = conn.execute("SELECT id FROM documents WHERE rel_path = ?", (rel_path,)).fetchone()[
        "id"
    ]
    conn.execute(
        "INSERT INTO search_items "
        "(document_id, rel_path, lane, source_name, source_kind, item_type, title, snippet, "
        "normalized_text, start_line, end_line, confidence, scope, modified_at, "
        "contradiction_count, evidence_count, entities_json, evidence_json, importance, "
        "tier, access_count, last_access_date, injected_count, confirmed_count, "
        "bad_recall_count, fact_key, invalidated_at, supersedes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            rel_path,
            lane,
            "test",
            "markdown",
            item_type,
            snippet,
            snippet,
            snippet.lower(),
            start_line,
            end_line,
            confidence,
            scope,
            "2026-03-24",
            0,
            0,
            entities_json,
            "[]",
            importance,
            tier,
            access_count,
            last_access_date,
            injected_count,
            confirmed_count,
            bad_recall_count,
            fact_key,
            invalidated_at,
            supersedes,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


# ---------------------------------------------------------------------------
# Full row conversion
# ---------------------------------------------------------------------------


def test_row_to_search_hit_basic_fields() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, snippet="Deploy uses blue/green.", tier="working")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)

    assert hit.path == "bank/world.md"
    assert hit.start_line == 1
    assert hit.end_line == 1
    assert hit.snippet == "Deploy uses blue/green."
    assert hit.lane == "memory"
    assert hit.item_type == "fact"
    assert hit.score == 1.0
    assert hit.backend == "sqlite_fts"
    assert hit.tier == "working"
    assert hit.item_id == item_id


def test_row_to_search_hit_entities_parsed() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, entities_json='["Alice", "Bob"]')
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.entities == ("Alice", "Bob")


def test_row_to_search_hit_confidence_and_importance() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, confidence=0.75, importance=0.6)
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.confidence is not None and abs(hit.confidence - 0.75) < 1e-6
    assert hit.importance is not None and abs(hit.importance - 0.6) < 1e-6


def test_row_to_search_hit_null_confidence() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, confidence=None)
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.confidence is None


def test_row_to_search_hit_null_importance() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, importance=None)
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.importance is None


def test_row_to_search_hit_fact_key() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, fact_key="user:name")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.fact_key == "user:name"


def test_row_to_search_hit_null_fact_key() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, fact_key=None)
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.fact_key is None


def test_row_to_search_hit_last_access_date() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, last_access_date="2026-03-24")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.last_access_date == "2026-03-24"


def test_row_to_search_hit_null_last_access_date() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, last_access_date=None)
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.last_access_date is None


def test_row_to_search_hit_access_counts() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(
        conn, access_count=5, injected_count=3, confirmed_count=2, bad_recall_count=1
    )
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.access_count == 5
    assert hit.injected_count == 3
    assert hit.confirmed_count == 2
    assert hit.bad_recall_count == 1


def test_row_to_search_hit_scope() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, scope="project:strongclaw")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.scope == "project:strongclaw"


def test_row_to_search_hit_tier_normalized_to_core() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, tier="core")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.tier == "core"


def test_row_to_search_hit_invalidated_at() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, invalidated_at="2026-04-01T10:00:00")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.invalidated_at == "2026-04-01T10:00:00"


def test_row_to_search_hit_supersedes() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, supersedes="abc123")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.supersedes == "abc123"


def test_row_to_search_hit_null_entities_json() -> None:
    conn = _make_conn()
    item_id = _insert_search_item(conn, entities_json="[]")
    row = conn.execute("SELECT * FROM search_items WHERE id = ?", (item_id,)).fetchone()
    hit = row_to_search_hit(_FakeTierNormalizer(), row)
    assert hit.entities == ()
