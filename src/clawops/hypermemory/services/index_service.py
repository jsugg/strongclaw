"""Derived index and backend-state persistence helpers.

This service owns SQLite-backed state and derived index operations.

Design goal: keep the dependency graph acyclic.
- IndexService never calls the vector backend.
- BackendService may call IndexService as a narrow "state store".
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from clawops.hypermemory.sparse import SparseEncoder


class IndexService:
    """Stateful façade over SQLite-backed derived index state."""

    def __init__(
        self,
        *,
        connect: Callable[[], sqlite3.Connection],
    ) -> None:
        self._connect = connect

    def count_rows(self, conn: sqlite3.Connection, table_name: str) -> int:
        """Count rows inside *table_name*."""
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return 0 if row is None else int(row["count"])

    def count_sparse_vector_items(self, conn: sqlite3.Connection) -> int:
        """Count indexed rows that carry sparse vector state."""
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM vector_items WHERE sparse_term_count > 0"
        ).fetchone()
        return 0 if row is None else int(row["count"])

    def backend_state_value(self, conn: sqlite3.Connection, key: str) -> str | None:
        """Return the current backend state value for *key*."""
        row = conn.execute("SELECT value FROM backend_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def write_backend_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        """Persist a backend state value."""
        conn.execute(
            "INSERT OR REPLACE INTO backend_state(key, value) VALUES (?, ?)",
            (key, value),
        )

    def write_sparse_state(
        self,
        conn: sqlite3.Connection,
        sparse_encoder: SparseEncoder,
        *,
        enabled: bool,
    ) -> None:
        """Persist sparse vocabulary metadata into the derived SQLite state."""
        if not enabled:
            self.write_backend_state(conn, "sparse_fingerprint", "")
            self.write_backend_state(conn, "sparse_doc_count", "0")
            self.write_backend_state(conn, "sparse_avg_doc_length", "0")
            return
        for term, term_id in sorted(sparse_encoder.term_to_id.items(), key=lambda item: item[1]):
            conn.execute(
                "INSERT INTO sparse_terms(term, term_id, document_freq) VALUES (?, ?, ?)",
                (term, term_id, int(sparse_encoder.document_frequency.get(term, 0))),
            )
        self.write_backend_state(conn, "sparse_fingerprint", sparse_encoder.fingerprint)
        self.write_backend_state(conn, "sparse_doc_count", str(sparse_encoder.document_count))
        self.write_backend_state(
            conn,
            "sparse_avg_doc_length",
            f"{sparse_encoder.average_document_length:.8f}",
        )

    def load_sparse_encoder(
        self,
        conn: sqlite3.Connection,
        *,
        enabled: bool,
    ) -> SparseEncoder | None:
        """Load the persisted sparse vocabulary from SQLite."""
        if not enabled:
            return None
        rows = conn.execute(
            "SELECT term, term_id, document_freq FROM sparse_terms ORDER BY term_id ASC"
        ).fetchall()
        if not rows:
            return None
        term_to_id = {str(row["term"]): int(row["term_id"]) for row in rows}
        document_frequency = {str(row["term"]): int(row["document_freq"]) for row in rows}
        document_count = int(self.backend_state_value(conn, "sparse_doc_count") or "0")
        average_document_length = float(
            self.backend_state_value(conn, "sparse_avg_doc_length") or "0"
        )
        fingerprint = self.backend_state_value(conn, "sparse_fingerprint") or ""
        return SparseEncoder(
            term_to_id=term_to_id,
            document_frequency=document_frequency,
            document_count=document_count,
            average_document_length=average_document_length,
            fingerprint=fingerprint,
        )
