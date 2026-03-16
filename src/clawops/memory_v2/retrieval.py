"""Retrieval planning and ranking for strongclaw memory v2."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from clawops.memory_v2.models import (
    TYPE_ORDER,
    EntryType,
    SearchExplanation,
    SearchHit,
    SearchMode,
    normalize_text_tokens,
)


def search_index(
    conn: sqlite3.Connection,
    *,
    query: str,
    max_results: int,
    min_score: float | None,
    mode: SearchMode,
    scope: str | None,
    ranking: Any,
    include_explain: bool,
) -> list[SearchHit]:
    """Run the dual-lane retrieval planner against the derived SQLite index."""
    terms = normalize_text_tokens(query)
    if not terms:
        return []
    lanes: tuple[str, ...]
    if mode == "all":
        lanes = ("memory", "corpus")
    else:
        lanes = (mode,)
    candidates: list[dict[str, Any]] = []
    for lane in lanes:
        candidates.extend(
            _lane_candidates(
                conn,
                lane=lane,
                terms=terms,
                scope=scope,
                limit=max_results * 4,
                ranking=ranking,
            )
        )
    if not candidates:
        return []
    merged: dict[tuple[str, int, int], dict[str, Any]] = {}
    for candidate in candidates:
        key = (candidate["rel_path"], candidate["start_line"], candidate["end_line"])
        existing = merged.get(key)
        if existing is None or candidate["score"] > existing["score"]:
            merged[key] = candidate
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    selected = _apply_diversity(
        ranked, limit=max_results, diversity_penalty=ranking.diversity_penalty
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
            )
        )
    return hits[:max_results]


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
    candidates = [_score_candidate(row, terms=terms, ranking=ranking, is_fts=True) for row in rows]
    if candidates:
        return candidates
    like_query = f"%{' '.join(terms).lower()}%"
    substring_rows = conn.execute(
        f"""
        SELECT
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
            0.0 AS rank
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
        _score_candidate(row, terms=terms, ranking=ranking, is_fts=False) for row in substring_rows
    ]


def _score_candidate(
    row: sqlite3.Row,
    *,
    terms: Sequence[str],
    ranking: Any,
    is_fts: bool,
) -> dict[str, Any]:
    """Convert a SQLite row into a scored candidate."""
    snippet = str(row["snippet"])
    combined_tokens = normalize_text_tokens(f"{row['rel_path']} {snippet}")
    term_matches = sum(1 for term in terms if term in combined_tokens)
    coverage_ratio = term_matches / len(terms)
    lexical_score = _lexical_score(float(row["rank"]), is_fts=is_fts)
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
    base_score = (
        lexical_score * ranking.lexical_weight * lane_weight * type_weight
    ) + coverage_boost
    final_score = max(0.0, base_score + confidence_boost + recency_boost - contradiction_penalty)
    return {
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
        "score": final_score,
        "explanation": SearchExplanation(
            lexical_score=lexical_score,
            lane_weight=lane_weight,
            type_weight=type_weight,
            coverage_boost=coverage_boost,
            confidence_boost=confidence_boost,
            recency_boost=recency_boost,
            contradiction_penalty=contradiction_penalty,
        ),
    }


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
    """Apply a small MMR-style novelty penalty to avoid duplicate snippets."""
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
            novelty_penalty=penalty,
        )
        selected.append(chosen)
    return selected


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Return a token-based similarity score for dedupe."""
    left_tokens = set(normalize_text_tokens(str(left["snippet"])))
    right_tokens = set(normalize_text_tokens(str(right["snippet"])))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(overlap) / len(union)
