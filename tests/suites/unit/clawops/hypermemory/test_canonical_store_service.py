"""Unit tests for hypermemory/services/canonical_store_service.py."""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import replace

import pytest

from clawops.hypermemory import load_config
from clawops.hypermemory.models import (
    FusionMode,
    HypermemoryConfig,
    ReindexSummary,
    SearchBackend,
    SearchHit,
    SearchMode,
)
from clawops.hypermemory.schema import ensure_schema
from clawops.hypermemory.services.canonical_store_service import (
    CanonicalStoreDeps,
    CanonicalStoreService,
)
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_config(tmp_path: pathlib.Path) -> HypermemoryConfig:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    return load_config(config_path)  # type: ignore[return-value]


class _FakeCanonicalStoreDeps:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        dirty: bool = False,
    ) -> None:
        self._conn = conn
        self._dirty = dirty
        self.reindex_calls: int = 0

    def connect(self) -> sqlite3.Connection:
        return self._conn

    def is_dirty(self) -> bool:
        return self._dirty

    def reindex(self, *, flush_metadata: bool = True) -> ReindexSummary:
        del flush_metadata
        self.reindex_calls += 1
        return ReindexSummary(files=0, chunks=0, dirty=False)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        min_score: float | None = None,
        lane: SearchMode = "all",
        scope: str | None = None,
        auto_index: bool = True,
        include_explain: bool = False,
        backend: SearchBackend | None = None,
        dense_candidate_pool: int | None = None,
        sparse_candidate_pool: int | None = None,
        fusion: FusionMode | None = None,
        include_invalidated: bool = False,
    ) -> list[SearchHit]:
        del (
            query,
            max_results,
            min_score,
            lane,
            scope,
            auto_index,
            include_explain,
            backend,
            dense_candidate_pool,
            sparse_candidate_pool,
            fusion,
            include_invalidated,
        )
        return []


def _make_service(
    config: HypermemoryConfig,
    conn: sqlite3.Connection,
    *,
    dirty: bool = False,
) -> tuple[CanonicalStoreService, _FakeCanonicalStoreDeps]:
    deps = _FakeCanonicalStoreDeps(conn=conn, dirty=dirty)
    canon_deps: CanonicalStoreDeps = deps  # type: ignore[assignment]
    svc = CanonicalStoreService(config=config, deps=canon_deps)
    return svc, deps


def _insert_memory_item(
    conn: sqlite3.Connection,
    *,
    item_type: str = "fact",
    access_count: int = 0,
    injected_count: int = 0,
    confirmed_count: int = 0,
    bad_recall_count: int = 0,
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("MEMORY.md", "/ws/MEMORY.md", "memory", "main", "abc", 3, "2026-01-01", "2026-01-01"),
    )
    doc_id = conn.execute("SELECT id FROM documents WHERE rel_path = ?", ("MEMORY.md",)).fetchone()[
        "id"
    ]
    conn.execute(
        "INSERT INTO search_items "
        "(document_id, rel_path, lane, source_name, source_kind, item_type, title, snippet, "
        "normalized_text, start_line, end_line, scope, modified_at, "
        "contradiction_count, evidence_count, entities_json, evidence_json, "
        "access_count, injected_count, confirmed_count, bad_recall_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            "MEMORY.md",
            "memory",
            "main",
            "markdown",
            item_type,
            "snippet",
            "snippet",
            "snippet",
            1,
            1,
            "global",
            "2026-01-01",
            0,
            0,
            "[]",
            "[]",
            access_count,
            injected_count,
            confirmed_count,
            bad_recall_count,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


def test_connect_delegates_to_deps(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, deps = _make_service(config, conn)
    assert svc.connect() is deps.connect()


def test_is_dirty_false_delegates_to_deps(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn, dirty=False)
    assert not svc.is_dirty()


def test_is_dirty_true_delegates_to_deps(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn, dirty=True)
    assert svc.is_dirty()


def test_reindex_delegates_to_deps(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, deps = _make_service(config, conn)
    svc.reindex()
    assert deps.reindex_calls == 1


# ---------------------------------------------------------------------------
# flush_metadata
# ---------------------------------------------------------------------------


def test_flush_metadata_returns_empty_when_no_db(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    # config.db_path points to .openclaw/test-hypermemory.sqlite which doesn't exist
    result = svc.flush_metadata()
    assert result["ok"] is True
    assert result["updatedFiles"] == 0
    assert result["updatedEntries"] == 0


# ---------------------------------------------------------------------------
# run_lifecycle
# ---------------------------------------------------------------------------


def test_run_lifecycle_decay_disabled_returns_zero_counts(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    cfg = replace(config, decay=replace(config.decay, enabled=False))
    conn = _make_conn()
    svc, _ = _make_service(cfg, conn)
    result = svc.run_lifecycle()
    assert result["ok"] is True
    assert result["evaluated"] == 0
    assert result["changed"] == 0


# ---------------------------------------------------------------------------
# record_access / record_injection / record_confirmation / record_bad_recall
# ---------------------------------------------------------------------------


def test_record_access_empty_ids_returns_zero(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    result = svc.record_access(item_ids=[])
    assert result == {"ok": True, "updated": 0}


def test_record_access_increments_access_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    item_id = _insert_memory_item(conn, access_count=3)
    svc, _ = _make_service(config, conn)
    result = svc.record_access(item_ids=[item_id])
    assert result["updated"] == 1
    row = conn.execute("SELECT access_count FROM search_items WHERE id = ?", (item_id,)).fetchone()
    assert row["access_count"] == 4


def test_record_injection_increments_injected_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    item_id = _insert_memory_item(conn, injected_count=2)
    svc, _ = _make_service(config, conn)
    svc.record_injection(item_ids=[item_id])
    row = conn.execute(
        "SELECT injected_count FROM search_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row["injected_count"] == 3


def test_record_confirmation_increments_confirmed_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    item_id = _insert_memory_item(conn, confirmed_count=1)
    svc, _ = _make_service(config, conn)
    svc.record_confirmation(item_ids=[item_id])
    row = conn.execute(
        "SELECT confirmed_count FROM search_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row["confirmed_count"] == 2


def test_record_bad_recall_increments_bad_recall_count(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    item_id = _insert_memory_item(conn, bad_recall_count=0)
    svc, _ = _make_service(config, conn)
    svc.record_bad_recall(item_ids=[item_id])
    row = conn.execute(
        "SELECT bad_recall_count FROM search_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row["bad_recall_count"] == 1


# ---------------------------------------------------------------------------
# get_fact
# ---------------------------------------------------------------------------


def test_get_fact_empty_key_returns_none(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    assert svc.get_fact("") is None
    assert svc.get_fact("   ") is None


def test_get_fact_unknown_key_returns_none(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    assert svc.get_fact("user:name") is None


# ---------------------------------------------------------------------------
# list_facts
# ---------------------------------------------------------------------------


def test_list_facts_empty_registry_returns_empty(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    assert svc.list_facts() == []


def test_list_facts_with_category_filter_returns_empty(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    assert svc.list_facts(category="user") == []


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_empty_text_raises_value_error(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    with pytest.raises(ValueError):
        svc.store(kind="fact", text="")


def test_store_noise_text_returns_noise_dict(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    # "hello" is 5 chars — below default min_text_length=10, classified as noise
    result = svc.store(kind="fact", text="hello")
    assert result["noise"] is True
    assert result["stored"] is False


def test_store_whitespace_only_raises_value_error(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    conn = _make_conn()
    svc, _ = _make_service(config, conn)
    with pytest.raises(ValueError):
        svc.store(kind="fact", text="   ")
