"""Reviewer packet dispatch for approval-gated operations."""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib

from clawops.common import write_json
from clawops.op_journal import Operation, OperationJournal

REVIEW_PACKET_VERSION = 1
LOCAL_DISPATCH_CHANNEL = "local_file"
_OWNER_ONLY_DIR_MODE = 0o700
_OWNER_ONLY_FILE_MODE = 0o600


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalDispatchOutcome:
    """Result of dispatching a reviewer packet for one operation."""

    operation: Operation
    artifact_path: pathlib.Path
    dispatched: bool
    channel: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the outcome for workflow/wrapper payloads."""
        payload: dict[str, object] = {
            "dispatched": self.dispatched,
            "channel": self.channel,
            "artifactPath": self.artifact_path.as_posix(),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _decode_json(value: str | None, *, field_name: str) -> object:
    """Decode one persisted JSON string while preserving malformed payloads."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"_decodeError": f"invalid JSON in {field_name}", "raw": value}


def _default_artifact_path(journal: OperationJournal, *, op_id: str) -> pathlib.Path:
    """Return the default on-disk reviewer packet path for one operation."""
    return journal.db_path.parent / "reviews" / f"{op_id}.json"


def _normalize_permissions(path: pathlib.Path, mode: int) -> None:
    """Apply owner-only permissions on supported hosts."""
    if os.name == "nt":
        return
    try:
        current_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return
    if current_mode != mode:
        path.chmod(mode)


def build_review_packet(operation: Operation) -> dict[str, object]:
    """Build a stable reviewer packet for one pending operation."""
    return {
        "version": REVIEW_PACKET_VERSION,
        "opId": operation.op_id,
        "status": operation.status,
        "scope": operation.scope,
        "kind": operation.kind,
        "trustZone": operation.trust_zone,
        "normalizedTarget": operation.normalized_target,
        "createdAtMs": operation.created_at_ms,
        "updatedAtMs": operation.updated_at_ms,
        "approvalRequired": bool(operation.approval_required),
        "review": {
            "mode": operation.review_mode,
            "target": operation.review_target,
            "status": operation.review_status,
            "payload": _decode_json(
                operation.review_payload_json, field_name="review_payload_json"
            ),
        },
        "policy": {
            "decision": operation.policy_decision,
            "detail": _decode_json(
                operation.policy_decision_json, field_name="policy_decision_json"
            ),
        },
        "executionContract": _decode_json(
            operation.execution_contract_json,
            field_name="execution_contract_json",
        ),
        "inputs": _decode_json(operation.inputs_json, field_name="inputs_json"),
    }


def dispatch_pending_approval(
    *,
    journal: OperationJournal,
    operation: Operation,
) -> ApprovalDispatchOutcome:
    """Write and register one durable reviewer packet for a pending operation."""
    if operation.status != "pending_approval":
        raise ValueError("dispatch requires a pending_approval operation")
    artifact_path = (
        pathlib.Path(operation.review_artifact_path).expanduser().resolve()
        if operation.review_artifact_path
        else _default_artifact_path(journal, op_id=operation.op_id)
    )
    payload = build_review_packet(operation)
    try:
        write_json(artifact_path, payload)
        _normalize_permissions(artifact_path.parent, _OWNER_ONLY_DIR_MODE)
        _normalize_permissions(artifact_path, _OWNER_ONLY_FILE_MODE)
        updated = journal.transition(
            operation.op_id,
            "pending_approval",
            review_artifact_path=artifact_path.as_posix(),
            review_status=operation.review_status or "pending",
        )
    except Exception as exc:  # pragma: no cover - exercised through failure handling tests
        return ApprovalDispatchOutcome(
            operation=operation,
            artifact_path=artifact_path,
            dispatched=False,
            channel=LOCAL_DISPATCH_CHANNEL,
            error=str(exc),
        )
    return ApprovalDispatchOutcome(
        operation=updated,
        artifact_path=artifact_path,
        dispatched=True,
        channel=LOCAL_DISPATCH_CHANNEL,
    )
