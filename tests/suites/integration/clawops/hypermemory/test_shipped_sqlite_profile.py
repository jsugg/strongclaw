"""Integration coverage for the shipped SQLite hypermemory profile."""

from __future__ import annotations

import pathlib
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, default_config_path, load_config


def test_shipped_sqlite_profile_reindexes_repo_without_duplicate_document_crash(
    tmp_path: pathlib.Path,
) -> None:
    config = load_config(default_config_path())
    engine = HypermemoryEngine(replace(config, db_path=tmp_path / "hypermemory.sqlite"))

    summary = engine.reindex()

    with engine.connect() as conn:
        duplicate_rows = conn.execute("""
            SELECT rel_path, COUNT(*) AS row_count
            FROM documents
            GROUP BY rel_path
            HAVING COUNT(*) > 1
            """).fetchall()
        row = conn.execute(
            "SELECT rel_path, source_name FROM documents WHERE rel_path LIKE ?",
            ("%platform/docs/ACP_WORKERS.md",),
        ).fetchone()

    assert summary.files > 0
    assert duplicate_rows == []
    assert row is not None
    assert str(row["rel_path"]).endswith("platform/docs/ACP_WORKERS.md")
    assert str(row["source_name"]) == "runbooks"
