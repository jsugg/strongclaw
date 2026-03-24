"""Retrieval planning and ranking for StrongClaw hypermemory."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

from clawops.hypermemory.lifecycle import compute_decay_score
from clawops.hypermemory.models import (
    DecayConfig,
    DenseSearchCandidate,
    EntryType,
    FeedbackConfig,
    HybridConfig,
    RankingConfig,
    RerankResponse,
    RetrievalExtensionsConfig,
    SearchBackend,
    SearchDiagnostics,
    SearchExplanation,
    SearchHit,
    SearchMode,
    SparseSearchCandidate,
    Tier,
    normalize_text_tokens,
)

TYPE_ORDER = {
    "fact": 1.0,
    "reflection": 0.95,
    "opinion": 0.9,
    "entity": 0.92,
    "proposal": 0.85,
    "paragraph": 0.88,
    "section": 0.82,
}


def search_index(
    conn: sqlite3.Connection,
    *,
    query: str,
    max_results: int,
    min_score: float | None,
    mode: SearchMode,
    scope: str | None,
    ranking: RankingConfig,
    hybrid: HybridConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
    retrieval: RetrievalExtensionsConfig,
    dense_candidates: Sequence[DenseSearchCandidate] | None,
    sparse_candidates: Sequence[SparseSearchCandidate] | None,
    active_backend: SearchBackend,
    rerank_scorer: Callable[[str, Sequence[str]], RerankResponse] | None,
    rerank_candidate_pool: int,
    include_explain: bool,
) -> tuple[list[SearchHit], SearchDiagnostics]:
    """Run the retrieval planner against the derived SQLite index."""
    terms = normalize_text_tokens(query)
    if not terms:
        return [], SearchDiagnostics()

    lexical_pool = max(hybrid.sparse_candidate_pool, 1)
    if retrieval.adaptive_pool:
        lexical_pool = _adaptive_pool_size(
            base_pool=lexical_pool,
            total_items=_count_items(conn),
            active_items=_count_active_items(conn),
            invalidated_ratio=_invalidated_ratio(conn),
            query_specificity=_estimate_query_specificity(query),
            has_scope_filter=scope is not None,
            max_multiplier=max(retrieval.adaptive_pool_max_multiplier, 1),
        )

    lexical_candidates: list[dict[str, Any]]
    lexical_ms = 0.0
    if active_backend == "qdrant_sparse_dense_hybrid":
        lexical_candidates = _sparse_candidates(
            conn,
            terms=terms,
            mode=mode,
            scope=scope,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
            sparse_candidates=sparse_candidates or (),
        )
    else:
        lexical_started_at = perf_counter()
        lexical_candidates = _lexical_candidates(
            conn,
            terms=terms,
            mode=mode,
            scope=scope,
            limit=max_results * lexical_pool,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
        )
        lexical_ms = (perf_counter() - lexical_started_at) * 1000.0

    sqlite_dense_started_at = perf_counter()
    dense_ranked_candidates = _dense_candidates(
        conn,
        terms=terms,
        mode=mode,
        scope=scope,
        ranking=ranking,
        decay=decay,
        feedback=feedback,
        dense_candidates=dense_candidates or (),
    )
    sqlite_dense_ms = (perf_counter() - sqlite_dense_started_at) * 1000.0
    if not lexical_candidates and not dense_ranked_candidates:
        return (
            [],
            SearchDiagnostics(
                lexical_ms=lexical_ms,
                sqlite_dense_ms=sqlite_dense_ms,
                sparse_candidates=len(lexical_candidates),
            ),
        )

    fusion_started_at = perf_counter()
    merged = _merge_candidates(
        lexical_candidates=lexical_candidates,
        dense_candidates=dense_ranked_candidates,
        ranking=ranking,
        hybrid=hybrid,
        active_backend=active_backend,
    )
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    fusion_ms = (perf_counter() - fusion_started_at) * 1000.0

    rerank_response = _apply_semantic_rerank(
        query=query,
        candidates=ranked,
        rerank_scorer=rerank_scorer,
        rerank_candidate_pool=rerank_candidate_pool,
        ranking=ranking,
    )
    rerank_attempted_candidates = (
        min(rerank_candidate_pool, len(ranked))
        if (
            rerank_response.applied
            or rerank_response.fail_open
            or rerank_response.error is not None
        )
        else 0
    )
    ranked = sorted(ranked, key=lambda item: item["score"], reverse=True)
    selected = _apply_diversity(
        ranked,
        limit=max_results,
        diversity_penalty=ranking.diversity_penalty,
    )
    hits: list[SearchHit] = []
    for candidate in selected:
        if min_score is not None and candidate["score"] < min_score:
            continue
        explanation = candidate["explanation"] if include_explain else None
        hits.append(
            SearchHit(
                item_id=candidate["item_id"],
                path=candidate["rel_path"],
                start_line=candidate["start_line"],
                end_line=candidate["end_line"],
                score=candidate["score"],
                snippet=candidate["snippet"],
                lane=candidate["lane"],
                item_type=candidate["item_type"],
                confidence=candidate["confidence"],
                entities=tuple(candidate["entities"]),
                scope=candidate["scope"],
                evidence_count=candidate["evidence_count"],
                contradiction_count=candidate["contradiction_count"],
                explanation=explanation,
                backend=candidate["backend"],
                importance=candidate["importance"],
                tier=candidate["tier"],
                access_count=candidate["access_count"],
                last_access_date=candidate["last_access_date"],
                injected_count=candidate["injected_count"],
                confirmed_count=candidate["confirmed_count"],
                bad_recall_count=candidate["bad_recall_count"],
                fact_key=candidate["fact_key"],
                invalidated_at=candidate["invalidated_at"],
                supersedes=candidate["supersedes"],
            )
        )
    selected_hits = hits[:max_results]
    return (
        selected_hits,
        SearchDiagnostics(
            lexical_ms=lexical_ms,
            sqlite_dense_ms=sqlite_dense_ms,
            fusion_ms=fusion_ms,
            rerank_ms=rerank_response.latency_ms,
            lexical_candidates=len(lexical_candidates),
            sparse_candidates=(
                len(lexical_candidates) if active_backend == "qdrant_sparse_dense_hybrid" else 0
            ),
            dense_candidates=len(dense_ranked_candidates),
            rerank_candidates=rerank_attempted_candidates,
            selected_candidates=len(selected_hits),
            rerank_applied=rerank_response.applied,
            rerank_fallback_used=rerank_response.fallback_used,
            rerank_fail_open=rerank_response.fail_open,
            rerank_provider=rerank_response.provider,
        ),
    )


def _lexical_candidates(
    conn: sqlite3.Connection,
    *,
    terms: Sequence[str],
    mode: SearchMode,
    scope: str | None,
    limit: int,
    ranking: RankingConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
) -> list[dict[str, Any]]:
    """Collect lexical candidates from SQLite FTS and fallback substring search."""
    candidates: list[dict[str, Any]] = []
    for lane in _lanes_for_mode(mode):
        candidates.extend(
            _lane_candidates(
                conn,
                lane=lane,
                terms=terms,
                scope=scope,
                limit=limit,
                ranking=ranking,
                decay=decay,
                feedback=feedback,
            )
        )
    candidates.sort(key=lambda item: item["text_score"], reverse=True)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["lexical_rank"] = rank
    return candidates


def _lane_candidates(
    conn: sqlite3.Connection,
    *,
    lane: str,
    terms: Sequence[str],
    scope: str | None,
    limit: int,
    ranking: RankingConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
) -> list[dict[str, Any]]:
    """Collect lane-local candidates from FTS and substring retrieval."""
    params: list[Any] = [lane]
    scope_clause = ""
    if scope:
        scope_clause = "AND (si.scope = ? OR si.scope = 'global')"
        params.append(scope)
    fts_query = " OR ".join(f'"{term}"' for term in terms)
    rows = conn.execute(
        f"""
        SELECT
            si.id,
            si.rel_path,
            si.start_line,
            si.end_line,
            si.snippet,
            si.lane,
            si.item_type,
            si.confidence,
            si.scope,
            si.evidence_count,
            si.contradiction_count,
            si.entities_json,
            si.modified_at,
            si.importance,
            si.tier,
            si.access_count,
            si.last_access_date,
            si.injected_count,
            si.confirmed_count,
            si.bad_recall_count,
            si.fact_key,
            si.invalidated_at,
            si.supersedes,
            bm25(search_items_fts) AS rank
        FROM search_items_fts
        JOIN search_items AS si ON si.id = search_items_fts.rowid
        WHERE search_items_fts MATCH ?
          AND si.lane = ?
          AND si.invalidated_at IS NULL
          {scope_clause}
        ORDER BY rank ASC
        LIMIT ?
        """,
        [fts_query, *params, limit],
    ).fetchall()
    candidates = [
        _score_candidate(
            row,
            terms=terms,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
            text_score=_lexical_score(float(row["rank"]), is_fts=True),
        )
        for row in rows
    ]
    if candidates:
        return candidates
    like_query = f"%{' '.join(terms).lower()}%"
    substring_rows = conn.execute(
        f"""
        SELECT
            id,
            rel_path,
            start_line,
            end_line,
            snippet,
            lane,
            item_type,
            confidence,
            scope,
            evidence_count,
            contradiction_count,
            entities_json,
            modified_at,
            importance,
            tier,
            access_count,
            last_access_date,
            injected_count,
            confirmed_count,
            bad_recall_count,
            fact_key,
            invalidated_at,
            supersedes
        FROM search_items
        WHERE lane = ?
          AND invalidated_at IS NULL
          AND lower(title || ' ' || snippet) LIKE ?
          {scope_clause.replace("si.", "")}
        ORDER BY length(snippet) ASC, rel_path ASC, start_line ASC
        LIMIT ?
        """,
        [lane, like_query, *([scope] if scope else []), limit],
    ).fetchall()
    return [
        _score_candidate(
            row,
            terms=terms,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
            text_score=_lexical_score(0.0, is_fts=False),
        )
        for row in substring_rows
    ]


def _dense_candidates(
    conn: sqlite3.Connection,
    *,
    terms: Sequence[str],
    mode: SearchMode,
    scope: str | None,
    ranking: RankingConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
    dense_candidates: Sequence[DenseSearchCandidate],
) -> list[dict[str, Any]]:
    """Load dense candidate rows from SQLite for ranked point hits."""
    if not dense_candidates:
        return []
    item_ids = [candidate.item_id for candidate in dense_candidates]
    rows = _load_rows_by_item_ids(conn, item_ids)
    rows_by_id = {int(row["id"]): row for row in rows}
    lanes = set(_lanes_for_mode(mode))
    ranked_candidates: list[dict[str, Any]] = []
    for rank, dense_hit in enumerate(dense_candidates, start=1):
        row = rows_by_id.get(dense_hit.item_id)
        if row is None:
            continue
        if str(row["lane"]) not in lanes:
            continue
        row_scope = str(row["scope"])
        if scope and row_scope not in {scope, "global"}:
            continue
        candidate = _score_candidate(
            row,
            terms=terms,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
            text_score=0.0,
            dense_score=_normalize_dense_score(dense_hit.score),
        )
        candidate["dense_rank"] = rank
        ranked_candidates.append(candidate)
    ranked_candidates.sort(key=lambda item: item["dense_score"], reverse=True)
    return ranked_candidates


def _sparse_candidates(
    conn: sqlite3.Connection,
    *,
    terms: Sequence[str],
    mode: SearchMode,
    scope: str | None,
    ranking: RankingConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
    sparse_candidates: Sequence[SparseSearchCandidate],
) -> list[dict[str, Any]]:
    """Load sparse candidate rows from SQLite for ranked Qdrant sparse hits."""
    if not sparse_candidates:
        return []
    item_ids = [candidate.item_id for candidate in sparse_candidates]
    rows = _load_rows_by_item_ids(conn, item_ids)
    rows_by_id = {int(row["id"]): row for row in rows}
    lanes = set(_lanes_for_mode(mode))
    ranked_candidates: list[dict[str, Any]] = []
    for rank, sparse_hit in enumerate(sparse_candidates, start=1):
        row = rows_by_id.get(sparse_hit.item_id)
        if row is None:
            continue
        if str(row["lane"]) not in lanes:
            continue
        row_scope = str(row["scope"])
        if scope and row_scope not in {scope, "global"}:
            continue
        candidate = _score_candidate(
            row,
            terms=terms,
            ranking=ranking,
            decay=decay,
            feedback=feedback,
            text_score=_normalize_sparse_score(sparse_hit.score),
        )
        candidate["lexical_rank"] = rank
        ranked_candidates.append(candidate)
    ranked_candidates.sort(key=lambda item: item["text_score"], reverse=True)
    return ranked_candidates


def _load_rows_by_item_ids(conn: sqlite3.Connection, item_ids: Sequence[int]) -> list[sqlite3.Row]:
    """Load search rows for the requested item IDs."""
    if not item_ids:
        return []
    placeholders = ", ".join("?" for _ in item_ids)
    return conn.execute(
        f"""
        SELECT
            id,
            rel_path,
            start_line,
            end_line,
            snippet,
            lane,
            item_type,
            confidence,
            scope,
            evidence_count,
            contradiction_count,
            entities_json,
            modified_at,
            importance,
            tier,
            access_count,
            last_access_date,
            injected_count,
            confirmed_count,
            bad_recall_count,
            fact_key,
            invalidated_at,
            supersedes
        FROM search_items
        WHERE id IN ({placeholders})
          AND invalidated_at IS NULL
        """,
        list(item_ids),
    ).fetchall()


def _merge_candidates(
    *,
    lexical_candidates: Sequence[Mapping[str, Any]],
    dense_candidates: Sequence[Mapping[str, Any]],
    ranking: RankingConfig,
    hybrid: HybridConfig,
    active_backend: SearchBackend,
) -> dict[int, dict[str, Any]]:
    """Merge lexical and dense candidate pools."""
    merged: dict[int, dict[str, Any]] = {}
    for candidate in lexical_candidates:
        item_id = int(candidate["item_id"])
        merged[item_id] = dict(candidate)
    for candidate in dense_candidates:
        item_id = int(candidate["item_id"])
        existing = merged.get(item_id)
        if existing is None:
            merged[item_id] = dict(candidate)
            continue
        existing["text_score"] = max(float(existing["text_score"]), float(candidate["text_score"]))
        existing["dense_score"] = max(
            float(existing["dense_score"]),
            float(candidate["dense_score"]),
        )
        existing["lexical_rank"] = existing.get("lexical_rank") or candidate.get("lexical_rank")
        existing["dense_rank"] = existing.get("dense_rank") or candidate.get("dense_rank")
    for candidate in merged.values():
        text_rank = _rank_value(candidate.get("lexical_rank"))
        dense_rank = _rank_value(candidate.get("dense_rank"))
        text_component = _text_component(
            score=float(candidate["text_score"]),
            rank=text_rank,
            ranking=ranking,
            hybrid=hybrid,
        )
        dense_component = _dense_component(
            score=float(candidate["dense_score"]),
            rank=dense_rank,
            hybrid=hybrid,
        )
        candidate["fusion_score"] = text_component + dense_component
        candidate["rerank_score"] = 0.0
        candidate["rerank_applied"] = False
        candidate["backend"] = (
            active_backend
            if (
                (dense_rank is not None or text_rank is not None)
                and active_backend in {"qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"}
            )
            else "sqlite_fts"
        )
        candidate["explanation"] = SearchExplanation(
            lexical_score=float(candidate["text_score"]),
            lane_weight=float(candidate["lane_weight"]),
            type_weight=float(candidate["type_weight"]),
            coverage_boost=float(candidate["coverage_boost"]),
            confidence_boost=float(candidate["confidence_boost"]),
            recency_boost=float(candidate["recency_boost"]),
            contradiction_penalty=float(candidate["contradiction_penalty"]),
            dense_score=float(candidate["dense_score"]),
            fusion_score=float(candidate["fusion_score"]),
            decay_boost=float(candidate.get("decay_boost", 0.0)),
            feedback_boost=float(candidate.get("feedback_boost", 0.0)),
            feedback_penalty=float(candidate.get("feedback_penalty", 0.0)),
        )
        _apply_candidate_score(candidate, ranking=ranking)
    return merged


def _apply_semantic_rerank(
    *,
    query: str,
    candidates: Sequence[dict[str, Any]],
    rerank_scorer: Callable[[str, Sequence[str]], RerankResponse] | None,
    rerank_candidate_pool: int,
    ranking: RankingConfig,
) -> RerankResponse:
    """Apply planner-stage reranking before diversity selection."""
    if rerank_scorer is None or rerank_candidate_pool <= 0 or not candidates:
        return RerankResponse()
    candidate_pool = list(candidates[:rerank_candidate_pool])
    documents = [_candidate_rerank_text(candidate) for candidate in candidate_pool]
    response = rerank_scorer(query, documents)
    if not response.applied:
        return response
    if len(response.scores) != len(candidate_pool):
        raise ValueError("rerank response count does not match the candidate pool size")
    for candidate, rerank_score in zip(candidate_pool, response.scores, strict=True):
        candidate["rerank_score"] = rerank_score
        candidate["rerank_applied"] = True
        _apply_candidate_score(candidate, ranking=ranking)
    return response


def _score_candidate(
    row: sqlite3.Row,
    *,
    terms: Sequence[str],
    ranking: RankingConfig,
    decay: DecayConfig,
    feedback: FeedbackConfig,
    text_score: float,
    dense_score: float = 0.0,
) -> dict[str, Any]:
    """Convert a SQLite row into a partially scored candidate."""
    snippet = str(row["snippet"])
    combined_tokens = normalize_text_tokens(f"{row['rel_path']} {snippet}")
    term_matches = sum(1 for term in terms if term in combined_tokens)
    coverage_ratio = term_matches / len(terms)
    lane_weight = (
        ranking.memory_lane_weight if row["lane"] == "memory" else ranking.corpus_lane_weight
    )
    type_weight = TYPE_ORDER.get(cast(EntryType, str(row["item_type"])), 0.9)
    confidence = None if row["confidence"] is None else float(row["confidence"])
    importance = None if row["importance"] is None else float(row["importance"])
    tier = cast(Tier, str(row["tier"] or "working"))
    access_count = int(row["access_count"] or 0)
    injected_count = int(row["injected_count"] or 0)
    confirmed_count = int(row["confirmed_count"] or 0)
    bad_recall_count = int(row["bad_recall_count"] or 0)
    confidence_boost = (confidence or 0.0) * ranking.confidence_weight
    recency_boost = _recency_boost(str(row["modified_at"]), ranking)
    decay_boost = 0.0
    if decay.enabled:
        decay_score = compute_decay_score(
            age_days=_age_days(str(row["modified_at"])),
            access_count=access_count,
            importance=importance if importance is not None else 0.5,
            tier=tier,
            config=decay,
        )
        tier_floor = {"core": 0.9, "working": 0.7, "peripheral": 0.5}.get(tier, 0.7)
        decay_boost = max(tier_floor, decay_score)
    contradiction_penalty = int(row["contradiction_count"]) * ranking.contradiction_penalty
    exact_coverage_bonus = ranking.coverage_weight if coverage_ratio == 1.0 else 0.0
    coverage_boost = coverage_ratio * ranking.coverage_weight + exact_coverage_bonus
    feedback_boost = 0.0
    feedback_penalty = 0.0
    if feedback.enabled and injected_count > 0:
        injection_ratio = confirmed_count / max(injected_count, 1)
        bad_ratio = bad_recall_count / max(injected_count, 1)
        feedback_boost = injection_ratio * feedback.reward_weight
        feedback_penalty = bad_ratio * feedback.penalty_weight
        if bad_recall_count >= feedback.suppress_threshold:
            feedback_penalty += feedback.suppress_penalty
    return {
        "item_id": int(row["id"]),
        "rel_path": str(row["rel_path"]),
        "start_line": int(row["start_line"]),
        "end_line": int(row["end_line"]),
        "snippet": snippet,
        "lane": str(row["lane"]),
        "item_type": str(row["item_type"]),
        "confidence": confidence,
        "scope": str(row["scope"]),
        "evidence_count": int(row["evidence_count"]),
        "contradiction_count": int(row["contradiction_count"]),
        "entities": tuple(json.loads(str(row["entities_json"]))),
        "importance": importance,
        "tier": tier,
        "access_count": access_count,
        "last_access_date": (
            None if row["last_access_date"] is None else str(row["last_access_date"])
        ),
        "injected_count": injected_count,
        "confirmed_count": confirmed_count,
        "bad_recall_count": bad_recall_count,
        "fact_key": None if row["fact_key"] is None else str(row["fact_key"]),
        "invalidated_at": None if row["invalidated_at"] is None else str(row["invalidated_at"]),
        "supersedes": None if row["supersedes"] is None else str(row["supersedes"]),
        "text_score": text_score,
        "dense_score": dense_score,
        "lane_weight": lane_weight,
        "type_weight": type_weight,
        "coverage_boost": coverage_boost,
        "confidence_boost": confidence_boost,
        "recency_boost": recency_boost,
        "decay_boost": decay_boost,
        "contradiction_penalty": contradiction_penalty,
        "feedback_boost": feedback_boost,
        "feedback_penalty": feedback_penalty,
        "fusion_score": 0.0,
        "rerank_score": 0.0,
        "rerank_applied": False,
        "score": 0.0,
        "backend": "sqlite_fts",
        "explanation": SearchExplanation(
            lexical_score=text_score,
            lane_weight=lane_weight,
            type_weight=type_weight,
            coverage_boost=coverage_boost,
            confidence_boost=confidence_boost,
            recency_boost=recency_boost,
            contradiction_penalty=contradiction_penalty,
            dense_score=dense_score,
            fusion_score=0.0,
            decay_boost=decay_boost,
            feedback_boost=feedback_boost,
            feedback_penalty=feedback_penalty,
        ),
    }


def _candidate_fused_base(candidate: Mapping[str, Any], *, ranking: RankingConfig) -> float:
    """Return the fused base score before governance boosts and penalties."""
    fusion_score = float(candidate["fusion_score"])
    if not bool(candidate.get("rerank_applied", False)):
        return fusion_score
    rerank_score = float(candidate.get("rerank_score", 0.0))
    rerank_weight = ranking.rerank_weight
    return (fusion_score * (1.0 - rerank_weight)) + (rerank_score * rerank_weight)


def _apply_candidate_score(candidate: dict[str, Any], *, ranking: RankingConfig) -> None:
    """Recompute the governed candidate score and explanation payload."""
    fused_base = _candidate_fused_base(candidate, ranking=ranking)
    candidate["score"] = max(
        0.0,
        (fused_base * float(candidate["lane_weight"]) * float(candidate["type_weight"]))
        + float(candidate["coverage_boost"])
        + float(candidate["confidence_boost"])
        + float(candidate["recency_boost"])
        + float(candidate.get("decay_boost", 0.0))
        + float(candidate.get("feedback_boost", 0.0))
        - float(candidate["contradiction_penalty"])
        - float(candidate.get("feedback_penalty", 0.0)),
    )
    explanation = cast(SearchExplanation, candidate["explanation"])
    candidate["explanation"] = SearchExplanation(
        lexical_score=explanation.lexical_score,
        lane_weight=explanation.lane_weight,
        type_weight=explanation.type_weight,
        coverage_boost=explanation.coverage_boost,
        confidence_boost=explanation.confidence_boost,
        recency_boost=explanation.recency_boost,
        contradiction_penalty=explanation.contradiction_penalty,
        dense_score=explanation.dense_score,
        fusion_score=float(candidate["fusion_score"]),
        rerank_score=float(candidate.get("rerank_score", 0.0)),
        decay_boost=float(candidate.get("decay_boost", 0.0)),
        feedback_boost=float(candidate.get("feedback_boost", 0.0)),
        feedback_penalty=float(candidate.get("feedback_penalty", 0.0)),
    )


def _candidate_rerank_text(candidate: Mapping[str, Any]) -> str:
    """Return the rerank text used to rescore one candidate."""
    return f"{candidate['rel_path']}\n{candidate['snippet']}"


def _text_component(
    *,
    score: float,
    rank: int | None,
    ranking: RankingConfig,
    hybrid: HybridConfig,
) -> float:
    """Return the lexical contribution to the fused retrieval score."""
    if rank is not None and hybrid.fusion == "rrf":
        return (score + (1.0 / (hybrid.rrf_k + rank))) * ranking.lexical_weight * hybrid.text_weight
    return score * ranking.lexical_weight * hybrid.text_weight


def _dense_component(*, score: float, rank: int | None, hybrid: HybridConfig) -> float:
    """Return the dense contribution to the fused retrieval score."""
    if rank is not None and hybrid.fusion == "rrf":
        return (score + (1.0 / (hybrid.rrf_k + rank))) * hybrid.vector_weight
    return score * hybrid.vector_weight


def _rank_value(value: object) -> int | None:
    """Return a validated integer rank or None."""
    if isinstance(value, int) and value > 0:
        return value
    return None


def _normalize_dense_score(raw_score: float) -> float:
    """Clamp dense similarity into a 0..1 range."""
    return max(0.0, min(raw_score, 1.0))


def _normalize_sparse_score(raw_score: float) -> float:
    """Compress sparse scores into a 0..1 range while preserving ordering."""
    if raw_score <= 0.0:
        return 0.0
    return raw_score / (1.0 + raw_score)


def _lexical_score(rank: float, *, is_fts: bool) -> float:
    """Convert SQLite FTS rank into a normalized lexical score."""
    if not is_fts:
        return 0.35
    return 1.0 / (1.0 + max(abs(rank), 0.01))


def _recency_boost(modified_at: str, ranking: RankingConfig) -> float:
    """Compute recency boost using an exponential decay."""
    age_days = _age_days(modified_at)
    if ranking.recency_half_life_days <= 0:
        return 0.0
    decay = math.exp(-math.log(2.0) * age_days / ranking.recency_half_life_days)
    return decay * ranking.recency_weight


def _age_days(modified_at: str) -> float:
    """Return the age of one timestamp in days."""
    try:
        timestamp = datetime.fromisoformat(modified_at)
    except ValueError:
        return 0.0
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max((datetime.now(tz=UTC) - timestamp).total_seconds() / 86400.0, 0.0)


def _adaptive_pool_size(
    *,
    base_pool: int,
    total_items: int,
    active_items: int,
    invalidated_ratio: float,
    query_specificity: float,
    has_scope_filter: bool,
    max_multiplier: int,
) -> int:
    """Compute an adaptive candidate pool size for larger stores."""
    effective_total = max(total_items, active_items)
    if effective_total <= 100:
        return base_pool
    size_factor = 1.0 + (0.3 * math.log2(max(effective_total / 100.0, 1.0)))
    crowding_factor = 1.0 + invalidated_ratio
    specificity_factor = 1.5 - (0.5 * query_specificity)
    scope_factor = 0.7 if has_scope_filter else 1.0
    adapted = int(base_pool * size_factor * crowding_factor * specificity_factor * scope_factor)
    return min(max(adapted, base_pool), base_pool * max(max_multiplier, 1))


def _estimate_query_specificity(query: str) -> float:
    """Estimate how specific a query is in the closed interval [0.0, 1.0]."""
    terms = normalize_text_tokens(query)
    if not terms:
        return 0.0
    term_count_factor = min(len(terms) / 6.0, 1.0)
    average_term_length = sum(len(term) for term in terms) / len(terms)
    length_factor = min(average_term_length / 8.0, 1.0)
    return (0.5 * term_count_factor) + (0.5 * length_factor)


def _count_items(conn: sqlite3.Connection) -> int:
    """Return the number of indexed items."""
    row = conn.execute("SELECT COUNT(*) FROM search_items").fetchone()
    return 0 if row is None else int(row[0])


def _count_active_items(conn: sqlite3.Connection) -> int:
    """Return the number of active, non-invalidated items."""
    row = conn.execute("SELECT COUNT(*) FROM search_items WHERE invalidated_at IS NULL").fetchone()
    return 0 if row is None else int(row[0])


def _invalidated_ratio(conn: sqlite3.Connection) -> float:
    """Return the invalidated share of the indexed store."""
    total = _count_items(conn)
    if total <= 0:
        return 0.0
    active = _count_active_items(conn)
    invalidated = max(total - active, 0)
    return invalidated / total


def _apply_diversity(
    candidates: Sequence[dict[str, Any]],
    *,
    limit: int,
    diversity_penalty: float,
) -> list[dict[str, Any]]:
    """Apply a small novelty penalty to avoid duplicate snippets."""
    selected: list[dict[str, Any]] = []
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        best_index = 0
        best_score = -1.0
        for index, candidate in enumerate(remaining):
            similarity = max((_similarity(candidate, chosen) for chosen in selected), default=0.0)
            adjusted = candidate["score"] - (similarity * diversity_penalty)
            if adjusted > best_score:
                best_score = adjusted
                best_index = index
        chosen = remaining.pop(best_index)
        explanation = cast(SearchExplanation, chosen["explanation"])
        penalty = max(chosen["score"] - best_score, 0.0)
        chosen["score"] = max(best_score, 0.0)
        chosen["explanation"] = SearchExplanation(
            lexical_score=explanation.lexical_score,
            lane_weight=explanation.lane_weight,
            type_weight=explanation.type_weight,
            coverage_boost=explanation.coverage_boost,
            confidence_boost=explanation.confidence_boost,
            recency_boost=explanation.recency_boost,
            contradiction_penalty=explanation.contradiction_penalty,
            dense_score=explanation.dense_score,
            fusion_score=explanation.fusion_score,
            rerank_score=explanation.rerank_score,
            novelty_penalty=penalty,
            decay_boost=explanation.decay_boost,
            feedback_boost=explanation.feedback_boost,
            feedback_penalty=explanation.feedback_penalty,
        )
        selected.append(chosen)
    return selected


def _similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    """Return a token-based similarity score for dedupe."""
    left_tokens = set(normalize_text_tokens(str(left["snippet"])))
    right_tokens = set(normalize_text_tokens(str(right["snippet"])))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(overlap) / len(union)


def _lanes_for_mode(mode: SearchMode) -> tuple[str, ...]:
    """Return the lane list for a search mode."""
    if mode == "all":
        return ("memory", "corpus")
    return (mode,)
