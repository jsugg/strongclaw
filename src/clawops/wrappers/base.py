"""Base wrapper logic shared by GitHub and webhook helpers."""

from __future__ import annotations

import dataclasses
import json
import os
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Mapping, cast

import requests

from clawops import __version__
from clawops.common import canonical_json
from clawops.observability import emit_structured_log, observed_span
from clawops.op_journal import Operation, OperationJournal
from clawops.policy_engine import TERMINAL_DENY, TERMINAL_REQUIRE_APPROVAL, Decision, PolicyEngine
from clawops.typed_values import as_string_list


def _empty_retryable_status_codes() -> frozenset[int]:
    """Return a typed empty retryable-status set."""
    return frozenset()


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
    error: dict[str, Any] | None = None
    error_type: str | None = None
    retryable: bool | None = None
    request_method: str | None = None
    request_url: str | None = None
    request_attempts: int | None = None
    request_id: str | None = None
    retry_after_seconds: float | None = None

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
        if self.error is not None:
            payload["error"] = self.error
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        if self.retryable is not None:
            payload["retryable"] = self.retryable
        if self.request_method is not None:
            payload["request_method"] = self.request_method
        if self.request_url is not None:
            payload["request_url"] = self.request_url
        if self.request_attempts is not None:
            payload["request_attempts"] = self.request_attempts
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


@dataclasses.dataclass(slots=True)
class PreparedOperation:
    """Prepared side-effect operation."""

    operation: Operation
    decision: Decision | None
    result: dict[str, Any] | None = None
    should_execute: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Endpoint-scoped retry policy for transport requests."""

    name: str
    max_attempts: int = 1
    retryable_status_codes: frozenset[int] = dataclasses.field(
        default_factory=_empty_retryable_status_codes
    )
    base_delay_seconds: float = 0.25
    jitter_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("retry base_delay_seconds must be non-negative")
        if self.jitter_seconds < 0:
            raise ValueError("retry jitter_seconds must be non-negative")

    @classmethod
    def no_retry(cls, *, name: str) -> "RetryPolicy":
        """Return an explicit no-retry policy for unsafe endpoints."""
        return cls(name=name, max_attempts=1)

    def allows_retry_for_exception(self, exc: requests.RequestException) -> bool:
        """Return whether the exception class is safe for automatic retry."""
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))

    def allows_retry_for_status(self, status_code: int) -> bool:
        """Return whether a status code is eligible for automatic retry."""
        return status_code in self.retryable_status_codes


NO_RETRY_POLICY = RetryPolicy.no_retry(name="no-retry")
type RetryMode = Literal["off", "safe"]


@dataclasses.dataclass(frozen=True, slots=True)
class HttpResponseOutcome:
    """One HTTP attempt sequence with request metadata."""

    response: requests.Response
    request_method: str
    request_url: str
    request_attempts: int
    retry_policy: RetryPolicy
    request_id: str | None = None
    retry_after_seconds: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class HttpTimeouts:
    """Split connect/read timeout configuration for wrapper transports."""

    connect_seconds: float
    read_seconds: float

    def __post_init__(self) -> None:
        if self.connect_seconds <= 0:
            raise ValueError("connect timeout must be positive")
        if self.read_seconds <= 0:
            raise ValueError("read timeout must be positive")

    def as_requests_timeout(self) -> tuple[float, float]:
        """Return the timeout tuple expected by `requests`."""
        return (self.connect_seconds, self.read_seconds)


class WrapperTransportError(RuntimeError):
    """Structured wrapper transport failure."""

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        method: str,
        url: str,
        retryable: bool,
        request_attempts: int,
        status_code: int | None = None,
        body_excerpt: str | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.method = method
        self.url = url
        self.retryable = retryable
        self.request_attempts = request_attempts
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds

    def error_message(self) -> str:
        """Return a journal-safe error summary."""
        if self.body_excerpt is not None and self.body_excerpt.strip():
            return self.body_excerpt[:1000]
        return str(self)[:1000]


class WrapperRequestError(WrapperTransportError):
    """Wrapped `requests` exception with stable metadata."""

    @classmethod
    def from_exception(
        cls,
        exc: requests.RequestException,
        *,
        method: str,
        url: str,
        retryable: bool,
        request_attempts: int,
    ) -> "WrapperRequestError":
        """Build a typed request error from `requests` exceptions."""
        if isinstance(exc, requests.Timeout):
            error_type = "timeout"
        elif isinstance(exc, requests.ConnectionError):
            error_type = "connection_error"
        else:
            error_type = "request_error"
        return cls(
            error_type=error_type,
            message=str(exc),
            method=method,
            url=url,
            retryable=retryable,
            request_attempts=request_attempts,
        )


class WrapperHttpStatusError(WrapperTransportError):
    """Wrapped HTTP error response with stable metadata."""

    @classmethod
    def from_response(
        cls,
        *,
        method: str,
        url: str,
        response: requests.Response,
        retryable: bool,
        request_attempts: int,
        observed_request_id: str | None = None,
        observed_retry_after_seconds: float | None = None,
    ) -> "WrapperHttpStatusError":
        """Build a typed response error from a non-success HTTP response."""
        body_excerpt = response.text[:1000]
        response_request_id = _response_request_id(response)
        response_retry_after_seconds = _parse_retry_after_seconds(
            response.headers.get("Retry-After")
        )
        message = (
            f"{method} {url} returned HTTP {response.status_code}"
            if not body_excerpt
            else body_excerpt[:1000]
        )
        return cls(
            error_type="http_status",
            message=message,
            method=method,
            url=url,
            retryable=retryable,
            request_attempts=request_attempts,
            status_code=response.status_code,
            body_excerpt=body_excerpt or None,
            request_id=response_request_id or observed_request_id,
            retry_after_seconds=(
                response_retry_after_seconds
                if response_retry_after_seconds is not None
                else observed_retry_after_seconds
            ),
        )


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
    if not isinstance(payload, Mapping):
        return None
    payload_mapping = cast(Mapping[str, object], payload)
    version = payload_mapping.get("version")
    scope = payload_mapping.get("scope")
    kind = payload_mapping.get("kind")
    trust_zone = payload_mapping.get("trust_zone")
    normalized_target = payload_mapping.get("normalized_target")
    inputs_hash = payload_mapping.get("inputs_hash")
    policy_decision = payload_mapping.get("policy_decision")
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
        if isinstance(payload, Mapping):
            payload_mapping = cast(Mapping[str, object], payload)
            decision = payload_mapping.get("decision")
            reasons = payload_mapping.get("reasons")
            matched_rules = payload_mapping.get("matched_rules")
            review_mode = payload_mapping.get("review_mode")
            review_target = payload_mapping.get("review_target")
            review_reason = payload_mapping.get("review_reason")
            review_policy_id = payload_mapping.get("review_policy_id")
            delegate_to = payload_mapping.get("delegate_to")
            if (
                isinstance(decision, str)
                and isinstance(reasons, list)
                and isinstance(matched_rules, list)
            ):
                return Decision(
                    decision=decision,
                    reasons=list(
                        as_string_list(cast(object, reasons), path="policy_decision.reasons")
                    ),
                    matched_rules=list(
                        as_string_list(
                            cast(object, matched_rules),
                            path="policy_decision.matched_rules",
                        )
                    ),
                    review_mode=review_mode if isinstance(review_mode, str) else None,
                    review_target=review_target if isinstance(review_target, str) else None,
                    review_reason=review_reason if isinstance(review_reason, str) else None,
                    review_policy_id=(
                        review_policy_id if isinstance(review_policy_id, str) else None
                    ),
                    delegate_to=delegate_to if isinstance(delegate_to, str) else None,
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
    review_payload = decision.review_payload()
    updated = ctx.journal.transition(
        op.op_id,
        op.status,
        policy_decision=decision.decision,
        policy_decision_json=_decision_to_json(decision),
        execution_contract_version=contract.version,
        execution_contract_json=_execution_contract_to_json(contract),
        approval_required=bool(op.approval_required)
        or decision.decision == TERMINAL_REQUIRE_APPROVAL,
        review_mode=decision.review_mode,
        review_target=decision.review_target,
        review_status=(
            "pending"
            if op.status == "pending_approval" and decision.decision == TERMINAL_REQUIRE_APPROVAL
            else op.review_status
        ),
        review_payload_json=None if not review_payload else canonical_json(review_payload),
    )
    return updated, decision


def _sleep_before_retry(policy: RetryPolicy, attempt_number: int) -> None:
    """Sleep before retrying a transport call."""
    if attempt_number >= policy.max_attempts:
        return
    delay = policy.base_delay_seconds * attempt_number
    if policy.jitter_seconds:
        delay += random.random() * policy.jitter_seconds
    if delay > 0:
        time.sleep(delay)


def _response_request_id(response: requests.Response) -> str | None:
    """Return a stable request identifier from known upstream headers."""
    for header_name in ("X-GitHub-Request-Id", "X-Request-Id"):
        value = response.headers.get(header_name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header into seconds when possible."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return max(float(stripped), 0.0)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)


def _coerce_http_outcome(result: HttpResponseOutcome | requests.Response) -> HttpResponseOutcome:
    """Normalize legacy raw responses into an outcome payload."""
    if isinstance(result, HttpResponseOutcome):
        return result
    return HttpResponseOutcome(
        response=result,
        request_method="UNKNOWN",
        request_url="",
        request_attempts=1,
        retry_policy=NO_RETRY_POLICY,
    )


def _http_retry_mode() -> RetryMode:
    """Return the retry mode configured for wrapper transports."""
    raw_value = os.environ.get("CLAWOPS_HTTP_RETRY_MODE", "off").strip().lower()
    if raw_value not in {"off", "safe"}:
        raise ValueError("CLAWOPS_HTTP_RETRY_MODE must be 'off' or 'safe'")
    return cast(RetryMode, raw_value)


def _effective_retry_policy(retry_policy: RetryPolicy | None) -> RetryPolicy:
    """Return the active retry policy after applying the environment gate."""
    policy = NO_RETRY_POLICY if retry_policy is None else retry_policy
    if _http_retry_mode() == "off" and policy.max_attempts > 1:
        return RetryPolicy.no_retry(name=policy.name)
    return policy


def _build_error_payload(
    *,
    error_type: str | None,
    message: str | None,
    status_code: int | None,
    retryable: bool | None,
    request_method: str | None,
    request_url: str | None,
    request_attempts: int | None,
    request_id: str | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Build a nested structured error payload when failure metadata exists."""
    if (
        error_type is None
        and message is None
        and status_code is None
        and retryable is None
        and request_method is None
        and request_url is None
        and request_attempts is None
        and request_id is None
        and retry_after_seconds is None
    ):
        return None
    payload: dict[str, Any] = {}
    if error_type is not None:
        payload["type"] = error_type
    if message is not None:
        payload["message"] = message
    if status_code is not None:
        payload["status_code"] = status_code
    if retryable is not None:
        payload["retryable"] = retryable
    if request_method is not None:
        payload["request_method"] = request_method
    if request_url is not None:
        payload["request_url"] = request_url
    if request_attempts is not None:
        payload["request_attempts"] = request_attempts
    if request_id is not None:
        payload["request_id"] = request_id
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = retry_after_seconds
    return payload


def _result_error_retryable(op: Operation) -> bool | None:
    """Return the persisted retryable flag, if any."""
    if op.result_error_retryable is None:
        return None
    return bool(op.result_error_retryable)


def _wrapper_observation_payload(
    *,
    op: Operation,
    ok: bool,
    executed: bool,
    status_code: int | None,
    error_type: str | None,
    retryable: bool | None,
    request_method: str | None,
    request_url: str | None,
    request_attempts: int | None,
    request_id: str | None,
    retry_after_seconds: float | None,
    elapsed_ms: int,
) -> dict[str, bool | int | float | str]:
    """Build a consistent wrapper observability payload."""
    payload: dict[str, bool | int | float | str] = {
        "op_id": op.op_id,
        "kind": op.kind,
        "target": op.normalized_target,
        "ok": ok,
        "executed": executed,
        "elapsed_ms": elapsed_ms,
        "status": op.status,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if error_type is not None:
        payload["error_type"] = error_type
    if retryable is not None:
        payload["retryable"] = retryable
    if request_method is not None:
        payload["request_method"] = request_method
    if request_url is not None:
        payload["request_url"] = request_url
    if request_attempts is not None:
        payload["request_attempts"] = request_attempts
    if request_id is not None:
        payload["request_id"] = request_id
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = retry_after_seconds
    return payload


def execute_http_operation(
    *,
    ctx: WrapperContext,
    op: Operation,
    decision: Decision | None,
    request: Callable[[], HttpResponseOutcome | requests.Response],
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
    started_at = time.perf_counter()
    with observed_span(
        "clawops.wrapper.execute",
        attributes={
            "op_id": op.op_id,
            "kind": op.kind,
            "scope": op.scope,
            "target": op.normalized_target,
        },
    ) as span:
        running = ctx.journal.transition(op.op_id, "running")
        try:
            outcome = _coerce_http_outcome(request())
            response = outcome.response
            if not response.ok:
                raise WrapperHttpStatusError.from_response(
                    method=outcome.request_method,
                    url=outcome.request_url,
                    response=response,
                    retryable=outcome.retry_policy.allows_retry_for_status(response.status_code),
                    request_attempts=outcome.request_attempts,
                    observed_request_id=outcome.request_id,
                    observed_retry_after_seconds=outcome.retry_after_seconds,
                )
        except WrapperTransportError as exc:
            message = exc.error_message()
            completed = ctx.journal.transition(
                running.op_id,
                "failed",
                error=message[:500],
                result_ok=False,
                result_status_code=exc.status_code,
                result_body_excerpt=message[:1000],
                result_error_type=exc.error_type,
                result_error_retryable=exc.retryable,
                result_request_method=exc.method,
                result_request_url=exc.url,
                result_request_attempts=exc.request_attempts,
                result_request_id=exc.request_id,
                result_retry_after_seconds=exc.retry_after_seconds,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observation = _wrapper_observation_payload(
                op=completed,
                ok=False,
                executed=True,
                status_code=exc.status_code,
                error_type=exc.error_type,
                retryable=exc.retryable,
                request_method=exc.method,
                request_url=exc.url,
                request_attempts=exc.request_attempts,
                request_id=exc.request_id,
                retry_after_seconds=exc.retry_after_seconds,
                elapsed_ms=elapsed_ms,
            )
            span.record_exception(exc)
            span.set_error(message[:250])
            span.set_attributes(observation)
            emit_structured_log("clawops.wrapper.execute", observation)
            return result_from_operation(
                completed,
                decision=decision,
                ok=False,
                accepted=True,
                executed=True,
                status_code=exc.status_code,
                body=message[:1000],
                error=_build_error_payload(
                    error_type=exc.error_type,
                    message=message[:1000],
                    status_code=exc.status_code,
                    retryable=exc.retryable,
                    request_method=exc.method,
                    request_url=exc.url,
                    request_attempts=exc.request_attempts,
                    request_id=exc.request_id,
                    retry_after_seconds=exc.retry_after_seconds,
                ),
                error_type=exc.error_type,
                retryable=exc.retryable,
                request_method=exc.method,
                request_url=exc.url,
                request_attempts=exc.request_attempts,
                request_id=exc.request_id,
                retry_after_seconds=exc.retry_after_seconds,
            )
        except requests.RequestException as exc:
            wrapped = WrapperRequestError.from_exception(
                exc,
                method="UNKNOWN",
                url=op.normalized_target,
                retryable=False,
                request_attempts=1,
            )
            message = wrapped.error_message()
            completed = ctx.journal.transition(
                running.op_id,
                "failed",
                error=message[:500],
                result_ok=False,
                result_body_excerpt=message[:1000],
                result_error_type=wrapped.error_type,
                result_error_retryable=wrapped.retryable,
                result_request_method=wrapped.method,
                result_request_url=wrapped.url,
                result_request_attempts=wrapped.request_attempts,
                result_request_id=wrapped.request_id,
                result_retry_after_seconds=wrapped.retry_after_seconds,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observation = _wrapper_observation_payload(
                op=completed,
                ok=False,
                executed=True,
                status_code=wrapped.status_code,
                error_type=wrapped.error_type,
                retryable=wrapped.retryable,
                request_method=wrapped.method,
                request_url=wrapped.url,
                request_attempts=wrapped.request_attempts,
                request_id=wrapped.request_id,
                retry_after_seconds=wrapped.retry_after_seconds,
                elapsed_ms=elapsed_ms,
            )
            span.record_exception(wrapped)
            span.set_error(message[:250])
            span.set_attributes(observation)
            emit_structured_log("clawops.wrapper.execute", observation)
            return result_from_operation(
                completed,
                decision=decision,
                ok=False,
                accepted=True,
                executed=True,
                body=message[:1000],
                error=_build_error_payload(
                    error_type=wrapped.error_type,
                    message=message[:1000],
                    status_code=wrapped.status_code,
                    retryable=wrapped.retryable,
                    request_method=wrapped.method,
                    request_url=wrapped.url,
                    request_attempts=wrapped.request_attempts,
                    request_id=wrapped.request_id,
                    retry_after_seconds=wrapped.retry_after_seconds,
                ),
                error_type=wrapped.error_type,
                retryable=wrapped.retryable,
                request_method=wrapped.method,
                request_url=wrapped.url,
                request_attempts=wrapped.request_attempts,
                request_id=wrapped.request_id,
                retry_after_seconds=wrapped.retry_after_seconds,
            )

        completed = ctx.journal.transition(
            running.op_id,
            "succeeded",
            result_ok=True,
            result_status_code=response.status_code,
            result_body_excerpt=response.text[:1000],
            result_request_method=outcome.request_method,
            result_request_url=outcome.request_url,
            result_request_attempts=outcome.request_attempts,
            result_request_id=outcome.request_id,
            result_retry_after_seconds=outcome.retry_after_seconds,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        observation = _wrapper_observation_payload(
            op=completed,
            ok=True,
            executed=True,
            status_code=response.status_code,
            error_type=None,
            retryable=None,
            request_method=outcome.request_method,
            request_url=outcome.request_url,
            request_attempts=outcome.request_attempts,
            request_id=outcome.request_id,
            retry_after_seconds=outcome.retry_after_seconds,
            elapsed_ms=elapsed_ms,
        )
        span.set_attributes(observation)
        emit_structured_log("clawops.wrapper.execute", observation)
        return result_from_operation(
            completed,
            decision=decision,
            ok=True,
            accepted=True,
            executed=True,
            status_code=response.status_code,
            body=response.text[:1000],
            request_method=outcome.request_method,
            request_url=outcome.request_url,
            request_attempts=outcome.request_attempts,
            request_id=outcome.request_id,
            retry_after_seconds=outcome.retry_after_seconds,
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
        review_payload = decision.review_payload()
        contract = _build_execution_contract(op, decision)
        updated = ctx.journal.transition(
            op.op_id,
            "pending_approval",
            policy_decision=decision.decision,
            policy_decision_json=_decision_to_json(decision),
            execution_contract_version=contract.version,
            execution_contract_json=_execution_contract_to_json(contract),
            approval_required=True,
            review_mode=decision.review_mode,
            review_target=decision.review_target,
            review_status="pending",
            review_payload_json=(None if not review_payload else canonical_json(review_payload)),
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
    error: dict[str, Any] | None = None,
    error_type: str | None = None,
    retryable: bool | None = None,
    request_method: str | None = None,
    request_url: str | None = None,
    request_attempts: int | None = None,
    request_id: str | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a consistent wrapper result envelope."""
    resolved_error_type = op.result_error_type if error_type is None else error_type
    resolved_retryable = _result_error_retryable(op) if retryable is None else retryable
    resolved_request_method = op.result_request_method if request_method is None else request_method
    resolved_request_url = op.result_request_url if request_url is None else request_url
    resolved_request_attempts = (
        op.result_request_attempts if request_attempts is None else request_attempts
    )
    resolved_request_id = op.result_request_id if request_id is None else request_id
    resolved_retry_after_seconds = (
        op.result_retry_after_seconds if retry_after_seconds is None else retry_after_seconds
    )
    resolved_status_code = op.result_status_code if status_code is None else status_code
    resolved_body = op.result_body_excerpt if body is None else body
    return WrapperResult(
        ok=ok,
        accepted=accepted,
        executed=executed,
        status=op.status,
        op_id=op.op_id,
        decision=None if decision is None else decision.to_dict(),
        dry_run=dry_run,
        status_code=resolved_status_code,
        body=resolved_body,
        error=(
            _build_error_payload(
                error_type=resolved_error_type,
                message=resolved_body,
                status_code=resolved_status_code,
                retryable=resolved_retryable,
                request_method=resolved_request_method,
                request_url=resolved_request_url,
                request_attempts=resolved_request_attempts,
                request_id=resolved_request_id,
                retry_after_seconds=resolved_retry_after_seconds,
            )
            if error is None and resolved_error_type is not None
            else error
        ),
        error_type=resolved_error_type,
        retryable=resolved_retryable,
        request_method=resolved_request_method,
        request_url=resolved_request_url,
        request_attempts=resolved_request_attempts,
        request_id=resolved_request_id,
        retry_after_seconds=resolved_retry_after_seconds,
    ).to_dict()


class JsonHttpClient:
    """Thin requests wrapper used by all external API helpers."""

    def __init__(self, timeout: int | float | HttpTimeouts = 30) -> None:
        self.timeout = timeout

    def _requests_timeout(self) -> float | tuple[float, float]:
        """Return a timeout value accepted by `requests`."""
        if isinstance(self.timeout, HttpTimeouts):
            return self.timeout.as_requests_timeout()
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        return float(self.timeout)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> HttpResponseOutcome:
        """Execute a JSON request with stable metadata and endpoint-scoped retries."""
        request_headers = dict(headers)
        request_headers.setdefault("User-Agent", f"clawops/{__version__}")
        effective_policy = _effective_retry_policy(retry_policy)
        observed_request_id: str | None = None
        observed_retry_after_seconds: float | None = None
        for attempt_number in range(1, effective_policy.max_attempts + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    json=json_body,
                    timeout=self._requests_timeout(),
                )
            except requests.RequestException as exc:
                retryable = (
                    effective_policy.max_attempts > 1
                    and effective_policy.allows_retry_for_exception(exc)
                )
                if retryable and attempt_number < effective_policy.max_attempts:
                    _sleep_before_retry(effective_policy, attempt_number)
                    continue
                raise WrapperRequestError.from_exception(
                    exc,
                    method=method,
                    url=url,
                    retryable=retryable,
                    request_attempts=attempt_number,
                ) from exc
            if (
                not response.ok
                and effective_policy.max_attempts > 1
                and effective_policy.allows_retry_for_status(response.status_code)
                and attempt_number < effective_policy.max_attempts
            ):
                response_request_id = _response_request_id(response)
                response_retry_after_seconds = _parse_retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                if response_request_id is not None:
                    observed_request_id = response_request_id
                if response_retry_after_seconds is not None:
                    observed_retry_after_seconds = response_retry_after_seconds
                _sleep_before_retry(effective_policy, attempt_number)
                continue
            response_request_id = _response_request_id(response) or observed_request_id
            response_retry_after_seconds = _parse_retry_after_seconds(
                response.headers.get("Retry-After")
            )
            if response_retry_after_seconds is None:
                response_retry_after_seconds = observed_retry_after_seconds
            return HttpResponseOutcome(
                response=response,
                request_method=method,
                request_url=url,
                request_attempts=attempt_number,
                retry_policy=effective_policy,
                request_id=response_request_id,
                retry_after_seconds=response_retry_after_seconds,
            )
        raise AssertionError("unreachable: request loop returned no response")

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        retry_policy: RetryPolicy | None = None,
    ) -> HttpResponseOutcome:
        """Execute a JSON POST."""
        return self.request(
            "POST", url, headers=headers, json_body=json_body, retry_policy=retry_policy
        )

    def put(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        retry_policy: RetryPolicy | None = None,
    ) -> HttpResponseOutcome:
        """Execute a JSON PUT."""
        return self.request(
            "PUT", url, headers=headers, json_body=json_body, retry_policy=retry_policy
        )
