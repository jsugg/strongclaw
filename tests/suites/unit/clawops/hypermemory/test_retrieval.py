"""Unit tests for hypermemory/retrieval.py."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from clawops.hypermemory.models import (
    DecayConfig,
    DenseSearchCandidate,
    FeedbackConfig,
    HybridConfig,
    RankingConfig,
    RerankResponse,
    RetrievalExtensionsConfig,
)
from clawops.hypermemory.retrieval import (
    adaptive_pool_size,
    count_active_items,
    count_items,
    estimate_query_specificity,
    invalidated_ratio,
    search_index,
)
from clawops.hypermemory.schema import ensure_schema

# ---------------------------------------------------------------------------
# SQLite test helpers
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection,
    *,
    rel_path: str,
    lane: str = "memory",
) -> int:
    conn.execute(
        "INSERT INTO documents "
        "(rel_path, abs_path, lane, source_name, sha256, line_count, modified_at, indexed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rel_path, f"/ws/{rel_path}", lane, "test", "abc", 5, "2026-03-01T00:00:00", "2026-03-24"),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM documents WHERE rel_path=?", (rel_path,)).fetchone()[0])


def _insert_item(
    conn: sqlite3.Connection,
    *,
    doc_id: int,
    rel_path: str,
    lane: str = "memory",
    item_type: str = "fact",
    snippet: str,
    scope: str = "global",
    tier: str = "working",
    importance: float | None = None,
    confidence: float | None = None,
    invalidated_at: str | None = None,
) -> int:
    title = snippet[:40]
    normalized = " ".join(t for t in snippet.lower().split() if t.isalnum())
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
            title,
            snippet,
            normalized,
            1,
            2,
            confidence,
            scope,
            "2026-03-01T00:00:00",
            0,
            0,
            "[]",
            "[]",
            importance,
            tier,
            0,
            None,
            0,
            0,
            0,
            None,
            invalidated_at,
            None,
        ),
    )
    conn.commit()
    item_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    # FTS insert
    conn.execute(
        "INSERT INTO search_items_fts(rowid, title, snippet, entities) VALUES (?,?,?,?)",
        (item_id, title, snippet, ""),
    )
    conn.commit()
    return item_id


def _default_configs() -> (
    tuple[RankingConfig, HybridConfig, DecayConfig, FeedbackConfig, RetrievalExtensionsConfig]
):
    return (
        RankingConfig(),
        HybridConfig(),
        DecayConfig(enabled=False),
        FeedbackConfig(enabled=False),
        RetrievalExtensionsConfig(adaptive_pool=False, adaptive_pool_max_multiplier=4),
    )


# ---------------------------------------------------------------------------
# count_items / count_active_items / invalidated_ratio
# ---------------------------------------------------------------------------


def test_count_items_empty_store() -> None:
    conn = _make_conn()
    assert count_items(conn) == 0


def test_count_active_items_empty_store() -> None:
    conn = _make_conn()
    assert count_active_items(conn) == 0


def test_count_items_after_inserts() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(conn, doc_id=doc_id, rel_path="MEMORY.md", snippet="deploy uses blue green")
    _insert_item(conn, doc_id=doc_id, rel_path="MEMORY.md", snippet="rotate gateway token")
    assert count_items(conn) == 2


def test_count_active_items_excludes_invalidated() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(conn, doc_id=doc_id, rel_path="MEMORY.md", snippet="active item")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="old invalidated item",
        invalidated_at="2026-01-01",
    )
    assert count_active_items(conn) == 1
    assert count_items(conn) == 2


def test_invalidated_ratio_zero_when_empty() -> None:
    conn = _make_conn()
    assert invalidated_ratio(conn) == 0.0


def test_invalidated_ratio_partial() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(conn, doc_id=doc_id, rel_path="MEMORY.md", snippet="active")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="invalidated",
        invalidated_at="2026-01-01",
    )
    ratio = invalidated_ratio(conn)
    assert abs(ratio - 0.5) < 1e-6


def test_invalidated_ratio_all_active() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(conn, doc_id=doc_id, rel_path="MEMORY.md", snippet="active item one")
    assert invalidated_ratio(conn) == 0.0


# ---------------------------------------------------------------------------
# adaptive_pool_size
# ---------------------------------------------------------------------------


def test_adaptive_pool_size_small_store_no_change() -> None:
    result = adaptive_pool_size(
        base_pool=10,
        total_items=50,
        active_items=50,
        invalidated_ratio=0.0,
        query_specificity=0.5,
        has_scope_filter=False,
        max_multiplier=4,
    )
    assert result == 10


def test_adaptive_pool_size_large_store_expands() -> None:
    result = adaptive_pool_size(
        base_pool=10,
        total_items=10000,
        active_items=10000,
        invalidated_ratio=0.0,
        query_specificity=0.5,
        has_scope_filter=False,
        max_multiplier=4,
    )
    assert result > 10


def test_adaptive_pool_size_scope_filter_shrinks_pool() -> None:
    base = adaptive_pool_size(
        base_pool=10,
        total_items=1000,
        active_items=1000,
        invalidated_ratio=0.0,
        query_specificity=0.5,
        has_scope_filter=False,
        max_multiplier=4,
    )
    scoped = adaptive_pool_size(
        base_pool=10,
        total_items=1000,
        active_items=1000,
        invalidated_ratio=0.0,
        query_specificity=0.5,
        has_scope_filter=True,
        max_multiplier=4,
    )
    assert scoped < base


def test_adaptive_pool_size_capped_at_max_multiplier() -> None:
    result = adaptive_pool_size(
        base_pool=10,
        total_items=1_000_000,
        active_items=1_000_000,
        invalidated_ratio=0.5,
        query_specificity=0.0,
        has_scope_filter=False,
        max_multiplier=3,
    )
    assert result <= 30  # base_pool * max_multiplier


def test_adaptive_pool_size_never_below_base() -> None:
    result = adaptive_pool_size(
        base_pool=10,
        total_items=200,
        active_items=200,
        invalidated_ratio=0.0,
        query_specificity=1.0,
        has_scope_filter=True,
        max_multiplier=4,
    )
    assert result >= 10


# ---------------------------------------------------------------------------
# estimate_query_specificity
# ---------------------------------------------------------------------------


def test_estimate_query_specificity_empty_query() -> None:
    assert estimate_query_specificity("") == 0.0


def test_estimate_query_specificity_single_short_word() -> None:
    score = estimate_query_specificity("a")
    assert 0.0 <= score <= 1.0


def test_estimate_query_specificity_many_long_words() -> None:
    query = "authentication authorization deployment infrastructure"
    score = estimate_query_specificity(query)
    assert score > 0.5


def test_estimate_query_specificity_range() -> None:
    score = estimate_query_specificity("gateway token deploy checklist rotation")
    assert 0.0 <= score <= 1.0


def test_estimate_query_specificity_longer_is_more_specific() -> None:
    short = estimate_query_specificity("token")
    long = estimate_query_specificity("authentication token rotation deployment gateway")
    assert long > short


# ---------------------------------------------------------------------------
# search_index — empty query
# ---------------------------------------------------------------------------


def test_search_index_empty_query_returns_empty() -> None:
    conn = _make_conn()
    ranking, hybrid, decay, feedback, retrieval = _default_configs()
    hits, _ = search_index(
        conn,
        query="",
        max_results=10,
        min_score=None,
        mode="all",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    assert hits == []


# ---------------------------------------------------------------------------
# search_index — sparse-only (SQLite FTS)
# ---------------------------------------------------------------------------


def test_search_index_sparse_finds_matching_item() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Deploy uses blue green cutovers for safety",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="deploy blue green",
        max_results=5,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )

    assert len(hits) >= 1
    assert hits[0].snippet == "Deploy uses blue green cutovers for safety"


def test_search_index_no_match_returns_empty() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Completely unrelated gateway token content",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="xyznonexistentterm",
        max_results=5,
        min_score=None,
        mode="all",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    assert hits == []


def test_search_index_scope_filter_limits_results() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Deploy uses blue green cutovers",
        scope="global",
    )
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Deploy uses blue green cutovers",
        scope="project:other",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="deploy blue green",
        max_results=10,
        min_score=None,
        mode="memory",
        scope="project:strongclaw",
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    # Only global scope matches (project:strongclaw not in results since only global passes)
    for hit in hits:
        assert hit.scope in {"global", "project:strongclaw"}


def test_search_index_min_score_filters_low_scored() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Deploy uses blue green cutovers for safety",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="deploy",
        max_results=5,
        min_score=999.0,  # absurdly high threshold
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    assert hits == []


def test_search_index_include_explain_populates_explanation() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Rotate gateway token before deploy",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="gateway token",
        max_results=5,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=True,
    )
    assert hits
    assert hits[0].explanation is not None
    assert hits[0].explanation.lexical_score > 0


def test_search_index_explain_false_no_explanation() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Gateway token rotation is required",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="gateway token",
        max_results=5,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    assert hits
    assert hits[0].explanation is None


def test_search_index_max_results_respected() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    for i in range(5):
        _insert_item(
            conn,
            doc_id=doc_id,
            rel_path="MEMORY.md",
            snippet=f"Deploy gateway token rotation step {i} important",
        )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="deploy gateway",
        max_results=2,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )
    assert len(hits) <= 2


# ---------------------------------------------------------------------------
# search_index — dense candidates path
# ---------------------------------------------------------------------------


def test_search_index_dense_candidates_used() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    item_id = _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Blue green deploy safety protocol",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()
    dense = [DenseSearchCandidate(item_id=item_id, point_id="abc", score=0.95)]

    hits, diagnostics = search_index(
        conn,
        query="deploy safety",
        max_results=5,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=dense,
        sparse_candidates=None,
        active_backend="qdrant_dense_hybrid",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )

    assert len(hits) >= 1
    assert diagnostics.dense_candidates >= 1


# ---------------------------------------------------------------------------
# search_index — rerank scorer callback
# ---------------------------------------------------------------------------


def test_search_index_rerank_scorer_invoked() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Rotate gateway token is required before deploy",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    invocations: list[tuple[str, list[str]]] = []

    def mock_reranker(query: str, docs: Sequence[str]) -> RerankResponse:
        invocations.append((query, list(docs)))
        return RerankResponse(
            scores=tuple(1.0 for _ in docs),
            provider="local-sentence-transformers",
            applied=True,
        )

    _, diagnostics = search_index(
        conn,
        query="gateway token",
        max_results=5,
        min_score=None,
        mode="memory",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=mock_reranker,
        rerank_candidate_pool=10,
        include_explain=False,
    )

    assert len(invocations) == 1
    assert diagnostics.rerank_applied


def test_search_index_diagnostics_structure() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="MEMORY.md")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="MEMORY.md",
        snippet="Deploy checklist gateway",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    _, diagnostics = search_index(
        conn,
        query="deploy checklist",
        max_results=5,
        min_score=None,
        mode="all",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )

    summary = diagnostics.to_dict()
    assert "lexicalMs" in summary
    assert "selectedCandidates" in summary


# ---------------------------------------------------------------------------
# search_index — corpus lane
# ---------------------------------------------------------------------------


def test_search_index_corpus_lane_returns_corpus_hits() -> None:
    conn = _make_conn()
    doc_id = _insert_document(conn, rel_path="docs/runbook.md", lane="corpus")
    _insert_item(
        conn,
        doc_id=doc_id,
        rel_path="docs/runbook.md",
        lane="corpus",
        snippet="Rotate gateway token before enabling new browser profile",
    )
    ranking, hybrid, decay, feedback, retrieval = _default_configs()

    hits, _ = search_index(
        conn,
        query="gateway token browser",
        max_results=5,
        min_score=None,
        mode="corpus",
        scope=None,
        ranking=ranking,
        hybrid=hybrid,
        decay=decay,
        feedback=feedback,
        retrieval=retrieval,
        dense_candidates=None,
        sparse_candidates=None,
        active_backend="sqlite_fts",
        rerank_scorer=None,
        rerank_candidate_pool=0,
        include_explain=False,
    )

    assert hits
    assert all(h.lane == "corpus" for h in hits)
