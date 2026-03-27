"""Durable run and stage state for devflow."""

from __future__ import annotations

import dataclasses
import pathlib
import sqlite3
import uuid
from typing import Final, Literal, cast

from clawops.common import canonical_json, utc_now_ms
from clawops.op_journal import OperationJournal

type DevflowRunStatus = Literal["planned", "running", "succeeded", "failed", "cancelled"]
type DevflowStageStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]

RUN_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "cancelled"})
STAGE_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "cancelled"})


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowRunRecord:
    """Persisted devflow run row."""

    run_id: str
    created_at_ms: int
    updated_at_ms: int
    status: DevflowRunStatus
    repo_root: str
    project_id: str
    workspace_id: str
    lane: str
    goal: str
    run_profile: str
    bootstrap_profile: str
    workflow_path: str
    plan_sha256: str
    current_stage_name: str | None
    requested_by: str
    resume_token: str | None
    summary_json: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DevflowRunRecord":
        """Build a record from a sqlite row."""
        payload = dict(row)
        return cls(**payload)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe run payload."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowStageRecord:
    """Persisted devflow stage row."""

    stage_id: str
    run_id: str
    stage_name: str
    stage_index: int
    role: str
    workspace_root: str
    op_id: str | None
    session_identity: str | None
    status: DevflowStageStatus
    retry_budget: int
    retry_count: int
    summary_path: str | None
    audit_path: str | None
    artifact_manifest_path: str | None
    started_at_ms: int | None
    finished_at_ms: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DevflowStageRecord":
        """Build a record from a sqlite row."""
        payload = dict(row)
        return cls(**payload)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe stage payload."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowStageEvent:
    """Persisted devflow stage event row."""

    event_id: str
    run_id: str
    stage_name: str
    event_kind: str
    payload_json: str | None
    created_at_ms: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DevflowStageEvent":
        """Build an event from a sqlite row."""
        payload = dict(row)
        return cls(**payload)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe event payload."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowRunView:
    """Aggregate devflow run view for status and audit surfaces."""

    run: DevflowRunRecord
    stages: tuple[DevflowStageRecord, ...]
    events: tuple[DevflowStageEvent, ...]

    def next_incomplete_stage(self) -> DevflowStageRecord | None:
        """Return the first incomplete stage, if any."""
        for stage in self.stages:
            if stage.status not in STAGE_TERMINAL_STATUSES:
                return stage
            if stage.status == "failed":
                return stage
        return None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe run view."""
        return {
            "run": self.run.to_dict(),
            "stages": [stage.to_dict() for stage in self.stages],
            "events": [event.to_dict() for event in self.events],
        }


def _connect(db_path: pathlib.Path) -> sqlite3.Connection:
    """Return an initialized sqlite connection through ``OperationJournal``."""
    journal = OperationJournal(db_path)
    journal.init()
    return journal.connect()


def _append_stage_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage_name: str,
    event_kind: str,
    payload: dict[str, object] | None = None,
) -> None:
    """Write one stage event row."""
    conn.execute(
        """
        INSERT INTO devflow_stage_event (
          event_id,
          run_id,
          stage_name,
          event_kind,
          payload_json,
          created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"evt_{uuid.uuid4().hex}",
            run_id,
            stage_name,
            event_kind,
            None if payload is None else canonical_json(payload),
            utc_now_ms(),
        ),
    )


def begin_run(
    db_path: pathlib.Path,
    *,
    run_id: str,
    repo_root: pathlib.Path,
    project_id: str,
    workspace_id: str,
    lane: str,
    goal: str,
    run_profile: str,
    bootstrap_profile: str,
    workflow_path: pathlib.Path,
    plan_sha256: str,
    requested_by: str,
    summary: dict[str, object] | None = None,
) -> DevflowRunRecord:
    """Create one devflow run row or fail on duplicate run id."""
    now = utc_now_ms()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO devflow_run (
              run_id,
              created_at_ms,
              updated_at_ms,
              status,
              repo_root,
              project_id,
              workspace_id,
              lane,
              goal,
              run_profile,
              bootstrap_profile,
              workflow_path,
              plan_sha256,
              current_stage_name,
              requested_by,
              resume_token,
              summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                now,
                now,
                "planned",
                repo_root.expanduser().resolve().as_posix(),
                project_id,
                workspace_id,
                lane,
                goal,
                run_profile,
                bootstrap_profile,
                workflow_path.expanduser().resolve().as_posix(),
                plan_sha256,
                None,
                requested_by,
                None,
                None if summary is None else canonical_json(summary),
            ),
        )
        row = conn.execute("SELECT * FROM devflow_run WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"failed to create devflow run {run_id}")
        return DevflowRunRecord.from_row(row)


def record_stage_started(
    db_path: pathlib.Path,
    *,
    run_id: str,
    stage_name: str,
    stage_index: int,
    role: str,
    workspace_root: pathlib.Path,
    retry_budget: int = 0,
    op_id: str | None = None,
    session_identity: str | None = None,
) -> DevflowStageRecord:
    """Insert or update one stage as running."""
    now = utc_now_ms()
    stage_id = f"{run_id}:{stage_name}"
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT retry_count FROM devflow_stage WHERE run_id = ? AND stage_name = ?",
            (run_id, stage_name),
        ).fetchone()
        retry_count = 0 if existing is None else int(existing["retry_count"]) + 1
        conn.execute(
            """
            INSERT INTO devflow_stage (
              stage_id,
              run_id,
              stage_name,
              stage_index,
              role,
              workspace_root,
              op_id,
              session_identity,
              status,
              retry_budget,
              retry_count,
              summary_path,
              audit_path,
              artifact_manifest_path,
              started_at_ms,
              finished_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_name) DO UPDATE SET
              workspace_root = excluded.workspace_root,
              op_id = excluded.op_id,
              session_identity = excluded.session_identity,
              status = excluded.status,
              retry_budget = excluded.retry_budget,
              retry_count = excluded.retry_count,
              started_at_ms = excluded.started_at_ms,
              finished_at_ms = NULL
            """,
            (
                stage_id,
                run_id,
                stage_name,
                stage_index,
                role,
                workspace_root.expanduser().resolve().as_posix(),
                op_id,
                session_identity,
                "running",
                retry_budget,
                retry_count,
                None,
                None,
                None,
                now,
                None,
            ),
        )
        conn.execute(
            """
            UPDATE devflow_run
            SET status = ?, current_stage_name = ?, updated_at_ms = ?, resume_token = NULL
            WHERE run_id = ?
            """,
            ("running", stage_name, now, run_id),
        )
        _append_stage_event(
            conn,
            run_id=run_id,
            stage_name=stage_name,
            event_kind="stage_started",
            payload={"workspace_root": workspace_root.expanduser().resolve().as_posix()},
        )
        if existing is not None:
            _append_stage_event(
                conn,
                run_id=run_id,
                stage_name=stage_name,
                event_kind="stage_retried",
                payload={"retry_count": retry_count},
            )
        row = conn.execute(
            "SELECT * FROM devflow_stage WHERE run_id = ? AND stage_name = ?",
            (run_id, stage_name),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to record stage start for {run_id}:{stage_name}")
        return DevflowStageRecord.from_row(row)


def _finish_stage(
    db_path: pathlib.Path,
    *,
    run_id: str,
    stage_name: str,
    status: DevflowStageStatus,
    summary_path: pathlib.Path | None = None,
    audit_path: pathlib.Path | None = None,
    artifact_manifest_path: pathlib.Path | None = None,
    payload: dict[str, object] | None = None,
) -> DevflowStageRecord:
    """Update one stage to a terminal status."""
    now = utc_now_ms()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE devflow_stage
            SET status = ?,
                summary_path = COALESCE(?, summary_path),
                audit_path = COALESCE(?, audit_path),
                artifact_manifest_path = COALESCE(?, artifact_manifest_path),
                finished_at_ms = ?
            WHERE run_id = ? AND stage_name = ?
            """,
            (
                status,
                None if summary_path is None else summary_path.expanduser().resolve().as_posix(),
                None if audit_path is None else audit_path.expanduser().resolve().as_posix(),
                (
                    None
                    if artifact_manifest_path is None
                    else artifact_manifest_path.expanduser().resolve().as_posix()
                ),
                now,
                run_id,
                stage_name,
            ),
        )
        run_status: DevflowRunStatus = (
            "running" if status == "succeeded" else cast(DevflowRunStatus, status)
        )
        conn.execute(
            """
            UPDATE devflow_run
            SET updated_at_ms = ?,
                status = ?,
                current_stage_name = ?
            WHERE run_id = ?
            """,
            (now, run_status, stage_name, run_id),
        )
        _append_stage_event(
            conn,
            run_id=run_id,
            stage_name=stage_name,
            event_kind=f"stage_{status}",
            payload=payload,
        )
        row = conn.execute(
            "SELECT * FROM devflow_stage WHERE run_id = ? AND stage_name = ?",
            (run_id, stage_name),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to finish stage {run_id}:{stage_name}")
        return DevflowStageRecord.from_row(row)


def record_stage_completed(
    db_path: pathlib.Path,
    *,
    run_id: str,
    stage_name: str,
    summary_path: pathlib.Path | None = None,
    audit_path: pathlib.Path | None = None,
    artifact_manifest_path: pathlib.Path | None = None,
) -> DevflowStageRecord:
    """Mark one stage as succeeded."""
    return _finish_stage(
        db_path,
        run_id=run_id,
        stage_name=stage_name,
        status="succeeded",
        summary_path=summary_path,
        audit_path=audit_path,
        artifact_manifest_path=artifact_manifest_path,
    )


def record_stage_failed(
    db_path: pathlib.Path,
    *,
    run_id: str,
    stage_name: str,
    summary_path: pathlib.Path | None = None,
    audit_path: pathlib.Path | None = None,
    artifact_manifest_path: pathlib.Path | None = None,
    reason: str | None = None,
) -> DevflowStageRecord:
    """Mark one stage as failed and create a resume token."""
    stage = _finish_stage(
        db_path,
        run_id=run_id,
        stage_name=stage_name,
        status="failed",
        summary_path=summary_path,
        audit_path=audit_path,
        artifact_manifest_path=artifact_manifest_path,
        payload={"reason": reason} if reason is not None else None,
    )
    with _connect(db_path) as conn:
        resume_token = f"resume_{uuid.uuid4().hex}"
        conn.execute(
            "UPDATE devflow_run SET resume_token = ?, updated_at_ms = ? WHERE run_id = ?",
            (resume_token, utc_now_ms(), run_id),
        )
    return stage


def mark_run_succeeded(
    db_path: pathlib.Path, *, run_id: str, summary: dict[str, object]
) -> DevflowRunRecord:
    """Mark one run as succeeded."""
    now = utc_now_ms()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE devflow_run
            SET status = ?, updated_at_ms = ?, current_stage_name = NULL, summary_json = ?
            WHERE run_id = ?
            """,
            ("succeeded", now, canonical_json(summary), run_id),
        )
        row = conn.execute("SELECT * FROM devflow_run WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"failed to mark run succeeded: {run_id}")
        return DevflowRunRecord.from_row(row)


def cancel_run(db_path: pathlib.Path, *, run_id: str, requested_by: str) -> DevflowRunRecord:
    """Cancel one non-terminal run."""
    now = utc_now_ms()
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM devflow_run WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown devflow run: {run_id}")
        run = DevflowRunRecord.from_row(row)
        if run.status in RUN_TERMINAL_STATUSES:
            raise ValueError(f"devflow run {run_id} is already terminal: {run.status}")
        conn.execute(
            """
            UPDATE devflow_run
            SET status = ?, updated_at_ms = ?, summary_json = ?
            WHERE run_id = ?
            """,
            (
                "cancelled",
                now,
                canonical_json({"cancelled_by": requested_by}),
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE devflow_stage
            SET status = ?, finished_at_ms = COALESCE(finished_at_ms, ?)
            WHERE run_id = ? AND status NOT IN ('succeeded', 'failed', 'cancelled')
            """,
            ("cancelled", now, run_id),
        )
        _append_stage_event(
            conn,
            run_id=run_id,
            stage_name=run.current_stage_name or "run",
            event_kind="run_cancelled",
            payload={"cancelled_by": requested_by},
        )
        updated = conn.execute("SELECT * FROM devflow_run WHERE run_id = ?", (run_id,)).fetchone()
        if updated is None:
            raise RuntimeError(f"failed to cancel run {run_id}")
        return DevflowRunRecord.from_row(updated)


def get_run(db_path: pathlib.Path, *, run_id: str) -> DevflowRunView:
    """Load one run with ordered stage rows and events."""
    with _connect(db_path) as conn:
        run_row = conn.execute("SELECT * FROM devflow_run WHERE run_id = ?", (run_id,)).fetchone()
        if run_row is None:
            raise KeyError(f"unknown devflow run: {run_id}")
        stage_rows = conn.execute(
            "SELECT * FROM devflow_stage WHERE run_id = ? ORDER BY stage_index ASC",
            (run_id,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM devflow_stage_event WHERE run_id = ? ORDER BY created_at_ms ASC",
            (run_id,),
        ).fetchall()
    return DevflowRunView(
        run=DevflowRunRecord.from_row(run_row),
        stages=tuple(DevflowStageRecord.from_row(row) for row in stage_rows),
        events=tuple(DevflowStageEvent.from_row(row) for row in event_rows),
    )


def resume_run(db_path: pathlib.Path, *, run_id: str) -> DevflowRunView:
    """Mark one run as resumed and return its current state."""
    view = get_run(db_path, run_id=run_id)
    if view.run.status not in {"failed", "planned", "running"}:
        raise ValueError(f"devflow run {run_id} is not resumable from state {view.run.status}")
    next_stage = view.next_incomplete_stage()
    if next_stage is None:
        raise ValueError(f"devflow run {run_id} has no incomplete stages to resume")
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE devflow_run
            SET status = ?, updated_at_ms = ?, current_stage_name = ?
            WHERE run_id = ?
            """,
            ("running", utc_now_ms(), next_stage.stage_name, run_id),
        )
        _append_stage_event(
            conn,
            run_id=run_id,
            stage_name=next_stage.stage_name,
            event_kind="run_resumed",
            payload={"resume_token": view.run.resume_token},
        )
    return get_run(db_path, run_id=run_id)


def list_stuck_runs(db_path: pathlib.Path, *, older_than_ms: int) -> tuple[DevflowRunRecord, ...]:
    """Return non-terminal runs that have not been updated recently."""
    cutoff = utc_now_ms() - older_than_ms
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM devflow_run
            WHERE status IN ('planned', 'running')
              AND updated_at_ms < ?
            ORDER BY updated_at_ms ASC
            """,
            (cutoff,),
        ).fetchall()
    return tuple(DevflowRunRecord.from_row(row) for row in rows)
