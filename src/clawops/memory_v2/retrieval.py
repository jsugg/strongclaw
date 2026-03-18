"""Retrieval planning and ranking for strongclaw memory v2."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from clawops.memory_v2.models import (
    DenseSearchCandidate,
    EntryType,
    HybridConfig,
    SearchBackend,
    SearchExplanation,
    SearchHit,
    SearchMode,
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
    ranking: Any,
    hybrid: HybridConfig,
    dense_candidates: Sequence[DenseSearchCandidate] | None,
    active_backend: SearchBackend,
    include_explain: bool,
) -> list[SearchHit]:
    """Run the retrieval planner against the derived SQLite index."""
    terms = normalize_text_tokens(query)
    if not terms:
        return []
    lexical_candidates = _lexical_candidates(
        conn,
        terms=terms,
        mode=mode,
        scope=scope,
        limit=max_results * max(hybrid.sparse_candidate_pool, 1),
        ranking=ranking,
    )
    dense_ranked_candidates = _dense_candidates(
        conn,
        terms=terms,
        mode=mode,
        scope=scope,
        ranking=ranking,
        dense_candidates=dense_candidates or (),
    )
    if not lexical_candidates and not dense_ranked_candidates:
        return []
    merged = _merge_candidates(
        lexical_candidates=lexical_candidates,
        dense_candidates=dense_ranked_candidates,
        ranking=ranking,
        hybrid=hybrid,
        active_backend=active_backend,
    )
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
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
            )
        )
    return hits[:max_results]


def _lexical_candidates(
    conn: sqlite3.Connection,
    *,
    terms: Sequence[str],
    mode: SearchMode,
    scope: str | None,
    limit: int,
    ranking: Any,
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
    ranking: Any,
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
            bm25(search_items_fts) AS rank
        FROM search_items_fts
        JOIN search_items AS si ON si.id = search_items_fts.rowid
        WHERE search_items_fts MATCH ?
          AND si.lane = ?
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
            modified_at
        FROM search_items
        WHERE lane = ?
          AND lower(title || ' ' || snippet) LIKE ?
          {scope_clause.replace('si.', '')}
        ORDER BY length(snippet) ASC, rel_path ASC, start_line ASC
        LIMIT ?
        """,
        [lane, like_query, *([scope] if scope else []), limit],
    ).fetchall()
    return [
        _score_candidate(
            row, terms=terms, ranking=ranking, text_score=_lexical_score(0.0, is_fts=False)
        )
        for row in substring_rows
    ]


def _dense_candidates(
    conn: sqlite3.Connection,
    *,
    terms: Sequence[str],
    mode: SearchMode,
    scope: str | None,
    ranking: Any,
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
            text_score=0.0,
            dense_score=_normalize_dense_score(dense_hit.score),
        )
        candidate["dense_rank"] = rank
        ranked_candidates.append(candidate)
    ranked_candidates.sort(key=lambda item: item["dense_score"], reverse=True)
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
            modified_at
        FROM search_items
        WHERE id IN ({placeholders})
        """,
        list(item_ids),
    ).fetchall()


def _merge_candidates(
    *,
    lexical_candidates: Sequence[Mapping[str, Any]],
    dense_candidates: Sequence[Mapping[str, Any]],
    ranking: Any,
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
            float(existing["dense_score"]), float(candidate["dense_score"])
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
        fusion_score = text_component + dense_component
        final_score = max(
            0.0,
            (fusion_score * float(candidate["lane_weight"]) * float(candidate["type_weight"]))
            + float(candidate["coverage_boost"])
            + float(candidate["confidence_boost"])
            + float(candidate["recency_boost"])
            - float(candidate["contradiction_penalty"]),
        )
        candidate["score"] = final_score
        candidate["backend"] = (
            active_backend
            if dense_rank is not None and active_backend == "qdrant_dense_hybrid"
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
            fusion_score=fusion_score,
        )
    return merged


def _score_candidate(
    row: sqlite3.Row,
    *,
    terms: Sequence[str],
    ranking: Any,
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
    confidence_boost = (confidence or 0.0) * ranking.confidence_weight
    recency_boost = _recency_boost(str(row["modified_at"]), ranking)
    contradiction_penalty = int(row["contradiction_count"]) * ranking.contradiction_penalty
    exact_coverage_bonus = ranking.coverage_weight if coverage_ratio == 1.0 else 0.0
    coverage_boost = coverage_ratio * ranking.coverage_weight + exact_coverage_bonus
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
        "text_score": text_score,
        "dense_score": dense_score,
        "lane_weight": lane_weight,
        "type_weight": type_weight,
        "coverage_boost": coverage_boost,
        "confidence_boost": confidence_boost,
        "recency_boost": recency_boost,
        "contradiction_penalty": contradiction_penalty,
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
        ),
    }


def _text_component(*, score: float, rank: int | None, ranking: Any, hybrid: HybridConfig) -> float:
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


def _lexical_score(rank: float, *, is_fts: bool) -> float:
    """Convert SQLite FTS rank into a normalized lexical score."""
    if not is_fts:
        return 0.35
    return 1.0 / (1.0 + max(abs(rank), 0.01))


def _recency_boost(modified_at: str, ranking: Any) -> float:
    """Compute recency boost using an exponential decay."""
    try:
        timestamp = datetime.fromisoformat(modified_at)
    except ValueError:
        return 0.0
    age_days = max((datetime.now(tz=UTC) - timestamp).total_seconds() / 86400.0, 0.0)
    if ranking.recency_half_life_days <= 0:
        return 0.0
    decay = math.exp(-math.log(2.0) * age_days / ranking.recency_half_life_days)
    return decay * ranking.recency_weight


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
        explanation = chosen["explanation"]
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
            novelty_penalty=penalty,
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
