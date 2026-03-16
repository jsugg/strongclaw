"""Base wrapper logic shared by GitHub, Jira, and webhook helpers."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any, Mapping

import requests

from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import TERMINAL_DENY, TERMINAL_REQUIRE_APPROVAL, Decision, PolicyEngine


@dataclasses.dataclass(slots=True)
class WrapperContext:
    """Shared wrapper dependencies."""

    policy_engine: PolicyEngine
    journal: OperationJournal
    dry_run: bool = False

    def evaluate(self, payload: Mapping[str, Any]) -> Decision:
        """Evaluate policy for the requested side effect."""
        return self.policy_engine.evaluate(payload)

    def begin(
        self, *, scope: str, kind: str, trust_zone: str, target: str, payload: dict[str, Any]
    ) -> str:
        """Create or reuse an operation journal entry and return the op_id."""
        op = self.journal.begin(
            scope=scope,
            kind=kind,
            trust_zone=trust_zone,
            normalized_target=target,
            inputs=payload,
        )
        return op.op_id


@dataclasses.dataclass(slots=True)
class WrapperResult:
    """Structured wrapper result."""

    ok: bool
    accepted: bool
    executed: bool
    status: str
    op_id: str
    decision: dict[str, Any] | None = None
    dry_run: bool = False
    status_code: int | None = None
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a JSON-serializable mapping."""
        payload: dict[str, Any] = {
            "ok": self.ok,
            "accepted": self.accepted,
            "executed": self.executed,
            "status": self.status,
            "op_id": self.op_id,
        }
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.dry_run:
            payload["dry_run"] = True
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.body is not None:
            payload["body"] = self.body
        return payload


@dataclasses.dataclass(slots=True)
class PreparedOperation:
    """Prepared side-effect operation."""

    operation: Operation
    decision: Decision | None
    result: dict[str, Any] | None = None
    should_execute: bool = False


EXECUTION_CONTRACT_VERSION = 1


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionContract:
    """Wrapper-authored execution contract persisted with executable rows."""

    version: int
    scope: str
    kind: str
    trust_zone: str
    normalized_target: str
    inputs_hash: str
    policy_decision: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable contract payload."""
        return {
            "version": self.version,
            "scope": self.scope,
            "kind": self.kind,
            "trust_zone": self.trust_zone,
            "normalized_target": self.normalized_target,
            "inputs_hash": self.inputs_hash,
            "policy_decision": self.policy_decision,
        }

    def matches(self, op: Operation) -> bool:
        """Return True when the contract still matches the stored row."""
        return (
            self.version == EXECUTION_CONTRACT_VERSION
            and self.scope == op.scope
            and self.kind == op.kind
            and self.trust_zone == op.trust_zone
            and self.normalized_target == op.normalized_target
            and self.inputs_hash == op.inputs_hash
            and self.policy_decision == op.policy_decision
        )


def _decision_to_json(decision: Decision) -> str:
    """Serialize a policy decision for stable replay."""
    return json.dumps(decision.to_dict(), separators=(",", ":"), sort_keys=True)


def _execution_contract_to_json(contract: ExecutionContract) -> str:
    """Serialize an execution contract for persistence."""
    return json.dumps(contract.to_dict(), separators=(",", ":"), sort_keys=True)


def _build_execution_contract(op: Operation, decision: Decision) -> ExecutionContract:
    """Create the execution contract for a wrapper-authored row."""
    return ExecutionContract(
        version=EXECUTION_CONTRACT_VERSION,
        scope=op.scope,
        kind=op.kind,
        trust_zone=op.trust_zone,
        normalized_target=op.normalized_target,
        inputs_hash=op.inputs_hash,
        policy_decision=decision.decision,
    )


def execution_contract_from_operation(op: Operation) -> ExecutionContract | None:
    """Load the stored execution contract, if present and valid."""
    if op.execution_contract_json is None:
        return None
    try:
        payload = json.loads(op.execution_contract_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    scope = payload.get("scope")
    kind = payload.get("kind")
    trust_zone = payload.get("trust_zone")
    normalized_target = payload.get("normalized_target")
    inputs_hash = payload.get("inputs_hash")
    policy_decision = payload.get("policy_decision")
    if (
        not isinstance(version, int)
        or not isinstance(scope, str)
        or not isinstance(kind, str)
        or not isinstance(trust_zone, str)
        or not isinstance(normalized_target, str)
        or not isinstance(inputs_hash, str)
        or not isinstance(policy_decision, str)
    ):
        return None
    if version != EXECUTION_CONTRACT_VERSION:
        return None
    if op.execution_contract_version is not None and op.execution_contract_version != version:
        return None
    return ExecutionContract(
        version=version,
        scope=scope,
        kind=kind,
        trust_zone=trust_zone,
        normalized_target=normalized_target,
        inputs_hash=inputs_hash,
        policy_decision=policy_decision,
    )


def decision_from_operation(op: Operation) -> Decision | None:
    """Load the stored policy decision, if present."""
    if op.policy_decision_json:
        try:
            payload = json.loads(op.policy_decision_json)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            decision = payload.get("decision")
            reasons = payload.get("reasons")
            matched_rules = payload.get("matched_rules")
            if (
                isinstance(decision, str)
                and isinstance(reasons, list)
                and all(isinstance(item, str) for item in reasons)
                and isinstance(matched_rules, list)
                and all(isinstance(item, str) for item in matched_rules)
            ):
                return Decision(
                    decision=decision, reasons=list(reasons), matched_rules=list(matched_rules)
                )
    if op.policy_decision is None:
        return None
    return Decision(decision=op.policy_decision, reasons=[], matched_rules=[])


def ensure_execution_contract(
    *,
    ctx: WrapperContext,
    op: Operation,
    decision_payload: Mapping[str, Any] | None,
) -> tuple[Operation, Decision | None]:
    """Backfill a missing execution contract when explicit policy is available."""
    decision = decision_from_operation(op)
    if execution_contract_from_operation(op) is not None:
        return op, decision
    if decision_payload is None or not ctx.policy_engine.policy:
        return op, decision

    decision = ctx.evaluate(decision_payload)
    if decision.decision == TERMINAL_DENY:
        raise ValueError(
            f"operation {op.op_id} is missing an execution contract and current policy denies it"
        )

    contract = _build_execution_contract(op, decision)
    updated = ctx.journal.transition(
        op.op_id,
        op.status,
        policy_decision=decision.decision,
        policy_decision_json=_decision_to_json(decision),
        execution_contract_version=contract.version,
        execution_contract_json=_execution_contract_to_json(contract),
        approval_required=bool(op.approval_required)
        or decision.decision == TERMINAL_REQUIRE_APPROVAL,
    )
    return updated, decision


def execute_http_operation(
    *,
    ctx: WrapperContext,
    op: Operation,
    decision: Decision | None,
    request: Callable[[], requests.Response],
) -> dict[str, Any]:
    """Run one journaled HTTP side effect with terminal failure handling."""
    existing = replay_result_from_operation(op, decision=decision)
    if existing is not None:
        return existing
    if op.status != "approved":
        raise ValueError(f"operation {op.op_id} is not executable from status {op.status}")
    contract = execution_contract_from_operation(op)
    if contract is None:
        raise ValueError(f"operation {op.op_id} is not executable: missing execution contract")
    if not contract.matches(op):
        raise ValueError(f"operation {op.op_id} is not executable: execution contract mismatch")
    running = ctx.journal.transition(op.op_id, "running")
    try:
        response = request()
    except requests.RequestException as exc:
        message = str(exc)
        completed = ctx.journal.transition(
            running.op_id,
            "failed",
            error=message[:500],
            result_ok=False,
            result_body_excerpt=message[:1000],
        )
        return result_from_operation(
            completed,
            decision=decision,
            ok=False,
            accepted=True,
            executed=True,
            body=message[:1000],
        )

    if response.ok:
        completed = ctx.journal.transition(
            running.op_id,
            "succeeded",
            result_ok=True,
            result_status_code=response.status_code,
            result_body_excerpt=response.text[:1000],
        )
    else:
        completed = ctx.journal.transition(
            running.op_id,
            "failed",
            error=response.text[:500],
            result_ok=False,
            result_status_code=response.status_code,
            result_body_excerpt=response.text[:1000],
        )
    return result_from_operation(
        completed,
        decision=decision,
        ok=response.ok,
        accepted=True,
        executed=True,
        status_code=response.status_code,
        body=response.text[:1000],
    )


def prepare_operation(
    *,
    ctx: WrapperContext,
    scope: str,
    kind: str,
    trust_zone: str,
    normalized_target: str,
    payload: dict[str, Any],
    decision_payload: Mapping[str, Any],
) -> PreparedOperation:
    """Prepare an operation and transition it into a pre-execution state."""
    op = ctx.journal.begin(
        scope=scope,
        kind=kind,
        trust_zone=trust_zone,
        normalized_target=normalized_target,
        inputs=payload,
    )
    if op.status != "proposed":
        decision = decision_from_operation(op)
        if op.status in {"pending_approval", "approved", "running"}:
            op, decision = ensure_execution_contract(
                ctx=ctx,
                op=op,
                decision_payload=decision_payload,
            )
        result = replay_result_from_operation(op, decision=decision, dry_run=ctx.dry_run)
        return PreparedOperation(
            op, decision, result=result, should_execute=op.status == "approved" and not ctx.dry_run
        )
    decision = ctx.evaluate(decision_payload)
    if decision.decision == TERMINAL_DENY:
        updated = ctx.journal.transition(
            op.op_id,
            "failed",
            error="policy denied",
            policy_decision=decision.decision,
            policy_decision_json=_decision_to_json(decision),
            approval_required=False,
            result_ok=False,
        )
        return PreparedOperation(
            updated,
            decision,
            result=result_from_operation(
                updated, decision=decision, ok=False, accepted=False, executed=False
            ),
        )
    if decision.decision == TERMINAL_REQUIRE_APPROVAL:
        contract = _build_execution_contract(op, decision)
        updated = ctx.journal.transition(
            op.op_id,
            "pending_approval",
            policy_decision=decision.decision,
            policy_decision_json=_decision_to_json(decision),
            execution_contract_version=contract.version,
            execution_contract_json=_execution_contract_to_json(contract),
            approval_required=True,
        )
        return PreparedOperation(
            updated,
            decision,
            result=result_from_operation(
                updated, decision=decision, ok=True, accepted=True, executed=False
            ),
        )
    contract = _build_execution_contract(op, decision)
    updated = ctx.journal.transition(
        op.op_id,
        "approved",
        policy_decision=decision.decision,
        policy_decision_json=_decision_to_json(decision),
        execution_contract_version=contract.version,
        execution_contract_json=_execution_contract_to_json(contract),
        approval_required=False,
    )
    if ctx.dry_run:
        return PreparedOperation(
            updated,
            decision,
            result=result_from_operation(
                updated, decision=decision, ok=True, accepted=True, executed=False, dry_run=True
            ),
        )
    return PreparedOperation(updated, decision, should_execute=True)


def replay_result_from_operation(
    op: Operation,
    *,
    decision: Decision | None,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Return a stable result for an already-existing operation when possible."""
    if op.status == "pending_approval":
        return result_from_operation(op, decision=decision, ok=True, accepted=True, executed=False)
    if op.status == "running":
        return result_from_operation(op, decision=decision, ok=True, accepted=True, executed=False)
    if op.status == "approved" and dry_run:
        return result_from_operation(
            op, decision=decision, ok=True, accepted=True, executed=False, dry_run=True
        )
    if op.status == "succeeded":
        return result_from_operation(
            op,
            decision=decision,
            ok=True if op.result_ok is None else bool(op.result_ok),
            accepted=True,
            executed=True,
            status_code=op.result_status_code,
            body=op.result_body_excerpt,
        )
    if op.status == "failed":
        accepted = not (op.policy_decision == TERMINAL_DENY and op.attempt == 0)
        executed = accepted and op.attempt > 0
        return result_from_operation(
            op,
            decision=decision,
            ok=False,
            accepted=accepted,
            executed=executed,
            status_code=op.result_status_code,
            body=op.result_body_excerpt,
        )
    if op.status == "cancelled":
        return result_from_operation(op, decision=decision, ok=False, accepted=True, executed=False)
    return None


def result_from_operation(
    op: Operation,
    *,
    decision: Decision | None,
    ok: bool,
    accepted: bool,
    executed: bool,
    dry_run: bool = False,
    status_code: int | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """Build a consistent wrapper result envelope."""
    return WrapperResult(
        ok=ok,
        accepted=accepted,
        executed=executed,
        status=op.status,
        op_id=op.op_id,
        decision=None if decision is None else decision.to_dict(),
        dry_run=dry_run,
        status_code=status_code,
        body=body,
    ).to_dict()


class JsonHttpClient:
    """Thin requests wrapper used by all external API helpers."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def post(
        self, url: str, *, headers: Mapping[str, str], json_body: Mapping[str, Any]
    ) -> requests.Response:
        """Execute a JSON POST."""
        return requests.post(url, headers=dict(headers), json=json_body, timeout=self.timeout)
