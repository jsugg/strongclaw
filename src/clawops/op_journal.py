"""SQLite-backed operation journal with idempotency support."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sqlite3
from typing import Any

from clawops.common import canonical_json, sha256_hex, utc_now_ms, write_json


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS op (
  op_id TEXT PRIMARY KEY,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  scope TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  trust_zone TEXT NOT NULL,
  normalized_target TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  inputs_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  compensation_state TEXT,
  UNIQUE(scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS op_status_idx ON op(status);
CREATE INDEX IF NOT EXISTS op_scope_idx  ON op(scope);
CREATE INDEX IF NOT EXISTS op_kind_idx   ON op(kind);
"""


@dataclasses.dataclass(slots=True)
class Operation:
    """Operation journal row."""

    op_id: str
    created_at_ms: int
    updated_at_ms: int
    scope: str
    idempotency_key: str
    kind: str
    trust_zone: str
    normalized_target: str
    inputs_json: str
    inputs_hash: str
    status: str
    attempt: int
    last_error: str | None
    compensation_state: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Operation":
        """Create an Operation from a sqlite row."""
        return cls(**dict(row))


class OperationJournal:
    """High-level journal API."""

    def __init__(self, db_path: pathlib.Path) -> None:
        self.db_path = db_path.expanduser()

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row access by name."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        """Create the journal schema."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def begin(
        self,
        *,
        scope: str,
        kind: str,
        trust_zone: str,
        normalized_target: str,
        inputs: dict[str, Any],
    ) -> Operation:
        """Start or deduplicate an operation."""
        now = utc_now_ms()
        normalized_inputs = canonical_json(inputs)
        idempotency_key = sha256_hex(
            canonical_json(
                {
                    "scope": scope,
                    "kind": kind,
                    "target": normalized_target,
                    "inputs": inputs,
                }
            )
        )
        op_id = sha256_hex(f"{scope}:{kind}:{normalized_target}:{now}")[:24]
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            existing = conn.execute(
                "SELECT * FROM op WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
            if existing:
                return Operation.from_row(existing)
            conn.execute(
                """
                INSERT INTO op (
                  op_id, created_at_ms, updated_at_ms, scope, idempotency_key,
                  kind, trust_zone, normalized_target, inputs_json, inputs_hash,
                  status, attempt, last_error, compensation_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op_id,
                    now,
                    now,
                    scope,
                    idempotency_key,
                    kind,
                    trust_zone,
                    normalized_target,
                    normalized_inputs,
                    sha256_hex(normalized_inputs),
                    "proposed",
                    0,
                    None,
                    None,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            assert row is not None
            return Operation.from_row(row)

    def transition(self, op_id: str, status: str, *, error: str | None = None) -> Operation:
        """Update operation state."""
        now = utc_now_ms()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {op_id}")
            attempt = row["attempt"] + (1 if status == "running" else 0)
            conn.execute(
                """
                UPDATE op
                SET updated_at_ms = ?, status = ?, attempt = ?, last_error = ?
                WHERE op_id = ?
                """,
                (now, status, attempt, error, op_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            assert updated is not None
            return Operation.from_row(updated)

    def list_stuck(self, *, older_than_ms: int) -> list[Operation]:
        """Return operations that are not terminal and stale."""
        cutoff = utc_now_ms() - older_than_ms
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM op
                WHERE status IN ('proposed', 'approved', 'running')
                  AND updated_at_ms < ?
                ORDER BY updated_at_ms ASC
                """,
                (cutoff,),
            ).fetchall()
            return [Operation.from_row(row) for row in rows]


def _load_payload(payload_file: pathlib.Path | None) -> dict[str, Any]:
    if payload_file is None:
        return {}
    return json.loads(payload_file.read_text(encoding="utf-8"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the op-journal CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init")
    init_parser.add_argument("--db", required=True, type=pathlib.Path)

    begin_parser = sub.add_parser("begin")
    begin_parser.add_argument("--db", required=True, type=pathlib.Path)
    begin_parser.add_argument("--scope", required=True)
    begin_parser.add_argument("--kind", required=True)
    begin_parser.add_argument("--trust-zone", required=True)
    begin_parser.add_argument("--target", required=True)
    begin_parser.add_argument("--payload-file", type=pathlib.Path)

    transition_parser = sub.add_parser("transition")
    transition_parser.add_argument("--db", required=True, type=pathlib.Path)
    transition_parser.add_argument("--op-id", required=True)
    transition_parser.add_argument("--status", required=True)
    transition_parser.add_argument("--error")

    list_parser = sub.add_parser("list-stuck")
    list_parser.add_argument("--db", required=True, type=pathlib.Path)
    list_parser.add_argument("--older-than-ms", required=True, type=int)
    list_parser.add_argument("--output", type=pathlib.Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    journal = OperationJournal(args.db)
    if args.command == "init":
        journal.init()
        return 0
    if args.command == "begin":
        op = journal.begin(
            scope=args.scope,
            kind=args.kind,
            trust_zone=args.trust_zone,
            normalized_target=args.target,
            inputs=_load_payload(args.payload_file),
        )
        print(json.dumps(dataclasses.asdict(op), sort_keys=True))
        return 0
    if args.command == "transition":
        op = journal.transition(args.op_id, args.status, error=args.error)
        print(json.dumps(dataclasses.asdict(op), sort_keys=True))
        return 0
    if args.command == "list-stuck":
        stuck = [dataclasses.asdict(op) for op in journal.list_stuck(older_than_ms=args.older_than_ms)]
        if args.output:
            write_json(args.output, stuck)
        else:
            print(json.dumps(stuck, indent=2, sort_keys=True))
        return 0
    raise AssertionError("unreachable")
