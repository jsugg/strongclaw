"""Search-hit mapping helpers.

This module provides the shared SQLite-row-to-SearchHit conversion used across
engine and services. It intentionally lives outside `_engine/*` so services do
not need to import private engine implementation modules.
"""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from clawops.hypermemory.canonical_store_helpers import normalize_tier
from clawops.hypermemory.models import Lane, SearchHit


def row_to_search_hit(row: sqlite3.Row) -> SearchHit:
    """Convert a SQLite row into a :class:`~clawops.hypermemory.models.SearchHit`.

    Args:
        row: SQLite row from the derived hypermemory schema.

    Returns:
        A structured search hit.
    """

    row_keys = set(row.keys())
    entities_json = row["entities_json"] if "entities_json" in row_keys else "[]"
    entities = tuple(json.loads(str(entities_json))) if entities_json is not None else ()

    return SearchHit(
        item_id=int(row["id"]) if "id" in row_keys and row["id"] is not None else None,
        path=str(row["rel_path"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        score=1.0,
        snippet=str(row["snippet"]),
        lane=cast(Lane, str(row["lane"])) if "lane" in row_keys else "memory",
        item_type=str(row["item_type"]) if "item_type" in row_keys else "fact",
        confidence=(
            None
            if "confidence" not in row_keys or row["confidence"] is None
            else float(row["confidence"])
        ),
        entities=entities,
        scope=str(row["scope"]) if "scope" in row_keys else None,
        evidence_count=int(row["evidence_count"]) if "evidence_count" in row_keys else 0,
        contradiction_count=(
            int(row["contradiction_count"]) if "contradiction_count" in row_keys else 0
        ),
        backend="sqlite_fts",
        importance=(
            None
            if "importance" not in row_keys or row["importance"] is None
            else float(row["importance"])
        ),
        tier=(
            normalize_tier(str(row["tier"]))
            if "tier" in row_keys and row["tier"] is not None
            else "working"
        ),
        access_count=int(row["access_count"]) if "access_count" in row_keys else 0,
        last_access_date=(
            None
            if "last_access_date" not in row_keys or row["last_access_date"] is None
            else str(row["last_access_date"])
        ),
        injected_count=int(row["injected_count"]) if "injected_count" in row_keys else 0,
        confirmed_count=int(row["confirmed_count"]) if "confirmed_count" in row_keys else 0,
        bad_recall_count=(int(row["bad_recall_count"]) if "bad_recall_count" in row_keys else 0),
        fact_key=(
            None if "fact_key" not in row_keys or row["fact_key"] is None else str(row["fact_key"])
        ),
        invalidated_at=(
            None
            if "invalidated_at" not in row_keys or row["invalidated_at"] is None
            else str(row["invalidated_at"])
        ),
        supersedes=(
            None
            if "supersedes" not in row_keys or row["supersedes"] is None
            else str(row["supersedes"])
        ),
    )
