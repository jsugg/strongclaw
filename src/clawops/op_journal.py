"""SQLite-backed operation journal with idempotency support."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sqlite3
import time
from typing import Any

from clawops.common import canonical_json, sha256_hex, utc_now_ms, write_json

SCHEMA_SQL = """
PRAGMA journal_mode=DELETE;
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
  policy_decision TEXT,
  policy_decision_json TEXT,
  execution_contract_version INTEGER,
  execution_contract_json TEXT,
  approval_required INTEGER NOT NULL DEFAULT 0,
  approved_by TEXT,
  approved_at_ms INTEGER,
  approval_note TEXT,
  result_ok INTEGER,
  result_status_code INTEGER,
  result_body_excerpt TEXT,
  UNIQUE(scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS op_status_idx ON op(status);
CREATE INDEX IF NOT EXISTS op_scope_idx  ON op(scope);
CREATE INDEX IF NOT EXISTS op_kind_idx   ON op(kind);
"""

MIGRATION_COLUMNS: dict[str, str] = {
    "policy_decision": "TEXT",
    "policy_decision_json": "TEXT",
    "execution_contract_version": "INTEGER",
    "execution_contract_json": "TEXT",
    "approval_required": "INTEGER NOT NULL DEFAULT 0",
    "approved_by": "TEXT",
    "approved_at_ms": "INTEGER",
    "approval_note": "TEXT",
    "result_ok": "INTEGER",
    "result_status_code": "INTEGER",
    "result_body_excerpt": "TEXT",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"failed", "pending_approval", "approved", "cancelled"},
    "pending_approval": {"approved", "failed", "cancelled"},
    "approved": {"running", "failed", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}

_UNSET = object()
_RETRYABLE_OPEN_ERROR = "unable to open database file"
_CONNECT_ATTEMPTS = 3
_CONNECT_RETRY_DELAY_SECONDS = 0.01


def _is_retryable_open_error(error: sqlite3.OperationalError) -> bool:
    """Return whether a SQLite operational error is worth retrying."""
    return _RETRYABLE_OPEN_ERROR in str(error).lower()


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
    policy_decision: str | None
    policy_decision_json: str | None
    execution_contract_version: int | None
    execution_contract_json: str | None
    approval_required: int
    approved_by: str | None
    approved_at_ms: int | None
    approval_note: str | None
    result_ok: int | None
    result_status_code: int | None
    result_body_excerpt: str | None

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
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                self._ensure_schema(conn)
            except sqlite3.OperationalError as exc:
                conn.close()
                if not _is_retryable_open_error(exc) or attempt == _CONNECT_ATTEMPTS:
                    raise
                time.sleep(_CONNECT_RETRY_DELAY_SECONDS * attempt)
                continue
            return conn
        raise AssertionError("unreachable: connect loop returned no SQLite connection")

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create and migrate the journal schema."""
        conn.executescript(SCHEMA_SQL)
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(op)").fetchall()}
        for name, definition in MIGRATION_COLUMNS.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE op ADD COLUMN {name} {definition}")

    def init(self) -> None:
        """Create the journal schema."""
        with self.connect() as conn:
            conn.commit()

    def get(self, op_id: str) -> Operation:
        """Return one operation by id."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {op_id}")
            return Operation.from_row(row)

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
                  status, attempt, last_error, compensation_state,
                  policy_decision, policy_decision_json, execution_contract_version, execution_contract_json,
                  approval_required, approved_by, approved_at_ms, approval_note,
                  result_ok, result_status_code, result_body_excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    None,
                    None,
                    None,
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            assert row is not None
            return Operation.from_row(row)

    def _validate_transition(self, current: str, new: str) -> None:
        """Validate a state transition."""
        if current == new:
            return
        allowed = ALLOWED_TRANSITIONS.get(current)
        if allowed is None or new not in allowed:
            raise ValueError(f"invalid operation transition: {current} -> {new}")

    def transition(
        self,
        op_id: str,
        status: str,
        *,
        error: str | None | object = _UNSET,
        policy_decision: str | None | object = _UNSET,
        policy_decision_json: str | None | object = _UNSET,
        execution_contract_version: int | None | object = _UNSET,
        execution_contract_json: str | None | object = _UNSET,
        approval_required: bool | object = _UNSET,
        result_ok: bool | None | object = _UNSET,
        result_status_code: int | None | object = _UNSET,
        result_body_excerpt: str | None | object = _UNSET,
    ) -> Operation:
        """Update operation state."""
        now = utc_now_ms()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {op_id}")
            current_status = str(row["status"])
            self._validate_transition(current_status, status)
            attempt = row["attempt"] + (
                1 if status == "running" and current_status != "running" else 0
            )
            next_error = row["last_error"] if error is _UNSET else error
            next_decision = row["policy_decision"] if policy_decision is _UNSET else policy_decision
            next_decision_json = (
                row["policy_decision_json"]
                if policy_decision_json is _UNSET
                else policy_decision_json
            )
            next_contract_version = (
                row["execution_contract_version"]
                if execution_contract_version is _UNSET
                else execution_contract_version
            )
            next_contract_json = (
                row["execution_contract_json"]
                if execution_contract_json is _UNSET
                else execution_contract_json
            )
            next_approval_required = (
                row["approval_required"]
                if approval_required is _UNSET
                else int(bool(approval_required))
            )
            next_result_ok = (
                row["result_ok"]
                if result_ok is _UNSET
                else (None if result_ok is None else int(bool(result_ok)))
            )
            next_result_status_code = (
                row["result_status_code"] if result_status_code is _UNSET else result_status_code
            )
            next_result_body_excerpt = (
                row["result_body_excerpt"] if result_body_excerpt is _UNSET else result_body_excerpt
            )
            conn.execute(
                """
                UPDATE op
                SET updated_at_ms = ?, status = ?, attempt = ?, last_error = ?,
                    policy_decision = ?, policy_decision_json = ?,
                    execution_contract_version = ?, execution_contract_json = ?,
                    approval_required = ?, result_ok = ?, result_status_code = ?, result_body_excerpt = ?
                WHERE op_id = ?
                """,
                (
                    now,
                    status,
                    attempt,
                    next_error,
                    next_decision,
                    next_decision_json,
                    next_contract_version,
                    next_contract_json,
                    next_approval_required,
                    next_result_ok,
                    next_result_status_code,
                    next_result_body_excerpt,
                    op_id,
                ),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            assert updated is not None
            return Operation.from_row(updated)

    def approve(self, op_id: str, *, approved_by: str, note: str | None = None) -> Operation:
        """Approve a pending operation."""
        now = utc_now_ms()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM op WHERE op_id = ?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {op_id}")
            current_status = str(row["status"])
            approval_required = bool(row["approval_required"])
            if approval_required and current_status != "pending_approval":
                raise ValueError(
                    f"approval-required operation must be pending_approval before approval: {current_status}"
                )
            self._validate_transition(current_status, "approved")
            conn.execute(
                """
                UPDATE op
                SET updated_at_ms = ?, status = ?, approved_by = ?, approved_at_ms = ?,
                    approval_note = ?, last_error = NULL
                WHERE op_id = ?
                """,
                (now, "approved", approved_by, now, note, op_id),
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
                WHERE status IN ('proposed', 'pending_approval', 'approved', 'running')
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

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--db", required=True, type=pathlib.Path)
    approve_parser.add_argument("--op-id", required=True)
    approve_parser.add_argument("--approved-by", required=True)
    approve_parser.add_argument("--note")

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
    if args.command == "approve":
        op = journal.approve(args.op_id, approved_by=args.approved_by, note=args.note)
        print(json.dumps(dataclasses.asdict(op), sort_keys=True))
        return 0
    if args.command == "list-stuck":
        stuck = [
            dataclasses.asdict(op) for op in journal.list_stuck(older_than_ms=args.older_than_ms)
        ]
        if args.output:
            write_json(args.output, stuck)
        else:
            print(json.dumps(stuck, indent=2, sort_keys=True))
        return 0
    raise AssertionError("unreachable")
