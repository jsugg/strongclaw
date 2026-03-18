"""Contract tests for policy-gated external wrappers."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Callable

import pytest
import requests
from pytest import MonkeyPatch

from clawops.common import write_yaml
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import JsonHttpClient, RetryPolicy, WrapperContext
from clawops.wrappers.github import (
    add_labels,
    create_comment,
    execute_github_approved,
    merge_pull_request,
)
from clawops.wrappers.webhook import execute_webhook_approved, invoke_webhook

type InvokeWrapper = Callable[[WrapperContext, str], dict[str, object]]
type ExecuteWrapper = Callable[[WrapperContext, str], dict[str, object]]
type AllowlistValue = Callable[[str], str]


@dataclasses.dataclass(frozen=True, slots=True)
class WrapperSpec:
    """Wrapper-specific lifecycle contract inputs."""

    name: str
    action: str
    category: str
    allowlist_key: str
    allowed_input: str
    denied_input: str
    normalized_target: str
    kind: str
    payload: dict[str, object]
    invoke: InvokeWrapper
    execute: ExecuteWrapper
    allowlist_value: AllowlistValue
    env: dict[str, str] = dataclasses.field(default_factory=dict)


class _FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, text: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text


def _identity(value: str) -> str:
    return value


def _invoke_webhook(ctx: WrapperContext, url: str) -> dict[str, object]:
    return invoke_webhook(
        ctx=ctx,
        url=url,
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_comment(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return create_comment(
        ctx=ctx,
        repo=repo,
        issue_number=123,
        body="hello",
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_labels(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return add_labels(
        ctx=ctx,
        repo=repo,
        issue_number=123,
        labels=["needs-review", "triaged"],
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_merge(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return merge_pull_request(
        ctx=ctx,
        repo=repo,
        pull_number=123,
        merge_method="squash",
        commit_title="Merge ready",
        commit_message="automerge",
        scope="test",
        trust_zone="automation",
    )


def _execute_webhook(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_webhook_approved(ctx=ctx, op_id=op_id)


def _execute_github(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_github_approved(ctx=ctx, op_id=op_id)


SPECS = (
    WrapperSpec(
        name="webhook",
        action="webhook.post",
        category="external_write",
        allowlist_key="webhook_url",
        allowed_input="https://example.internal/hooks/deploy",
        denied_input="https://evil.invalid/hooks/deploy",
        normalized_target="https://example.internal/hooks/deploy",
        kind="webhook_post",
        payload={"ok": True},
        invoke=_invoke_webhook,
        execute=_execute_webhook,
        allowlist_value=_identity,
    ),
    WrapperSpec(
        name="github-comment",
        action="github.comment.create",
        category="external_write",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/issues/123",
        kind="github_comment",
        payload={"body": "hello"},
        invoke=_invoke_github_comment,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
    WrapperSpec(
        name="github-labels",
        action="github.issue.labels.add",
        category="external_write",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/issues/123",
        kind="github_issue_labels",
        payload={"labels": ["needs-review", "triaged"]},
        invoke=_invoke_github_labels,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
    WrapperSpec(
        name="github-merge",
        action="github.pull_request.merge",
        category="irreversible",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/pulls/123/merge",
        kind="github_pull_merge",
        payload={
            "merge_method": "squash",
            "commit_title": "Merge ready",
            "commit_message": "automerge",
        },
        invoke=_invoke_github_merge,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
)


def _build_context(
    tmp_path: pathlib.Path,
    spec: WrapperSpec,
    *,
    require_approval: bool,
    dry_run: bool = False,
) -> tuple[WrapperContext, OperationJournal]:
    policy_path = tmp_path / f"{spec.name}-policy.yaml"
    policy: dict[str, object] = {
        "defaults": {"decision": "allow"},
        "zones": {
            "automation": {
                "allow_actions": [spec.action],
                "allow_categories": [spec.category],
            }
        },
        "allowlists": {
            spec.allowlist_key: [spec.allowlist_value(spec.allowed_input)],
        },
    }
    if require_approval:
        policy["approval"] = {"require_for_actions": [spec.action]}
    write_yaml(policy_path, policy)

    journal = OperationJournal(tmp_path / f"{spec.name}-journal.sqlite")
    journal.init()
    ctx = WrapperContext(
        policy_engine=PolicyEngine.from_file(policy_path), journal=journal, dry_run=dry_run
    )
    return ctx, journal


def _configure_wrapper_environment(spec: WrapperSpec, monkeypatch: MonkeyPatch) -> None:
    for key, value in spec.env.items():
        monkeypatch.setenv(key, value)


def _install_success_response(monkeypatch: MonkeyPatch, calls: list[str]) -> None:
    def _request(*args: object, **kwargs: object) -> _FakeResponse:
        calls.append("request")
        return _FakeResponse(text="ok")

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)


def _install_transport_error(
    monkeypatch: MonkeyPatch,
    message: str,
    calls: list[str] | None = None,
) -> None:
    def _request(*args: object, **kwargs: object) -> _FakeResponse:
        if calls is not None:
            calls.append("request")
        raise requests.Timeout(message)

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)


def _allow_decision_json() -> str:
    return json.dumps(
        {
            "decision": "allow",
            "matched_rules": [],
            "reasons": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_denies_non_allowlisted_target(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False, dry_run=True)
    _configure_wrapper_environment(spec, monkeypatch)

    result = spec.invoke(ctx, spec.denied_input)

    assert result["ok"] is False
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["status"] == "failed"

    persisted = journal.get(str(result["op_id"]))
    assert persisted.policy_decision == "deny"
    assert persisted.attempt == 0
    assert persisted.execution_contract_json is None


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_requires_explicit_approval_then_replays_terminal_result(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=True)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    prepared = spec.invoke(ctx, spec.allowed_input)

    assert prepared["ok"] is True
    assert prepared["accepted"] is True
    assert prepared["executed"] is False
    assert prepared["status"] == "pending_approval"

    approved = journal.approve(str(prepared["op_id"]), approved_by="operator")
    assert approved.status == "approved"

    executed = spec.execute(ctx, str(prepared["op_id"]))
    replayed = spec.execute(ctx, str(prepared["op_id"]))

    assert executed["ok"] is True
    assert executed["executed"] is True
    assert executed["status"] == "succeeded"
    assert replayed == executed
    assert calls == ["request"]

    persisted = journal.get(str(prepared["op_id"]))
    assert persisted.approved_by == "operator"
    assert persisted.result_ok == 1
    assert persisted.result_status_code == 200
    assert persisted.result_body_excerpt == "ok"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None
    assert persisted.result_request_method == (
        "PUT" if spec.kind == "github_pull_merge" else "POST"
    )
    assert persisted.result_request_attempts == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_pending_approval_without_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=True)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["status"] == "pending_approval"
    assert first["ok"] is True
    assert first["accepted"] is True
    assert first["executed"] is False
    assert second == first
    assert calls == []

    persisted = journal.get(str(first["op_id"]))
    assert persisted.status == "pending_approval"
    assert persisted.approval_required == 1
    assert persisted.attempt == 0
    assert persisted.execution_contract_version == 1
    assert persisted.review_mode == "manual"
    assert persisted.review_status == "pending"
    assert persisted.review_payload_json is not None


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_success_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is True
    assert first["executed"] is True
    assert first["status"] == "succeeded"
    assert second["ok"] is True
    assert second["executed"] is True
    assert second["status"] == "succeeded"
    assert second["body"] == "ok"
    assert second["request_attempts"] == 1
    assert calls == ["request"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.result_ok == 1
    assert persisted.result_status_code == 200
    assert persisted.result_body_excerpt == "ok"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1
    if spec.kind == "github_pull_merge":
        assert second["request_method"] == "PUT"
        assert persisted.result_request_method == "PUT"
        assert second["request_url"] == "https://api.github.com/repos/example/repo/pulls/123/merge"
    else:
        assert second["request_method"] == "POST"
        assert persisted.result_request_method == "POST"
    assert persisted.result_request_attempts == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_failed_terminal_result_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_transport_error(monkeypatch, "simulated timeout", calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is False
    assert first["accepted"] is True
    assert first["executed"] is True
    assert first["status"] == "failed"
    assert first["body"] == "simulated timeout"
    assert first["error_type"] == "timeout"
    assert first["retryable"] is False
    assert first["request_attempts"] == 1
    assert second == first
    assert calls == ["request"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.status == "failed"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
    assert persisted.result_error_type == "timeout"
    assert persisted.result_error_retryable == 0
    assert persisted.result_request_method == (
        "PUT" if spec.kind == "github_pull_merge" else "POST"
    )
    assert persisted.result_request_attempts == 1
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_transport_error_transitions_to_failed_terminal_state(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    _install_transport_error(monkeypatch, "simulated timeout")

    result = spec.invoke(ctx, spec.allowed_input)

    assert result["ok"] is False
    assert result["accepted"] is True
    assert result["executed"] is True
    assert result["status"] == "failed"
    assert result["body"] == "simulated timeout"
    assert result["error_type"] == "timeout"
    assert result["retryable"] is False
    assert result["request_method"] == ("PUT" if spec.kind == "github_pull_merge" else "POST")
    assert result["request_attempts"] == 1

    persisted = journal.get(str(result["op_id"]))
    assert persisted.status == "failed"
    assert persisted.last_error == "simulated timeout"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
    assert persisted.result_error_type == "timeout"
    assert persisted.result_error_retryable == 0
    assert persisted.result_request_method == (
        "PUT" if spec.kind == "github_pull_merge" else "POST"
    )
    assert persisted.result_request_attempts == 1
    assert persisted.attempt == 1
    assert journal.list_stuck(older_than_ms=0) == []
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_running_operation_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    op = journal.begin(
        scope="test",
        kind=spec.kind,
        trust_zone="automation",
        normalized_target=spec.normalized_target,
        inputs=spec.payload,
    )
    approved = journal.transition(
        op.op_id,
        "approved",
        policy_decision="allow",
        policy_decision_json=_allow_decision_json(),
        approval_required=False,
    )
    _ = journal.transition(approved.op_id, "running")

    replayed = spec.invoke(ctx, spec.allowed_input)

    assert replayed["ok"] is True
    assert replayed["accepted"] is True
    assert replayed["executed"] is False
    assert replayed["status"] == "running"
    assert replayed["decision"]["decision"] == "allow"
    assert calls == []

    persisted = journal.get(str(replayed["op_id"]))
    assert persisted.status == "running"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_stored_decision_when_policy_changes(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    first = spec.invoke(ctx, spec.allowed_input)

    deny_policy_path = tmp_path / f"{spec.name}-deny-policy.yaml"
    write_yaml(
        deny_policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "automation": {
                    "allow_actions": [spec.action],
                    "allow_categories": [spec.category],
                }
            },
            "allowlists": {
                spec.allowlist_key: [spec.allowlist_value(spec.denied_input)],
            },
        },
    )
    deny_ctx = WrapperContext(
        policy_engine=PolicyEngine.from_file(deny_policy_path),
        journal=journal,
        dry_run=False,
    )
    _configure_wrapper_environment(spec, monkeypatch)

    replayed = spec.invoke(deny_ctx, spec.allowed_input)

    assert first["status"] == "succeeded"
    assert replayed["status"] == "succeeded"
    assert replayed["decision"]["decision"] == "allow"
    assert replayed["decision"] == first["decision"]
    assert calls == ["request"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.policy_decision == "allow"
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_execute_approved_rejects_manual_rows_without_execution_contract(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    op = journal.begin(
        scope="test",
        kind=spec.kind,
        trust_zone="automation",
        normalized_target=spec.normalized_target,
        inputs=spec.payload,
    )
    approved = journal.approve(op.op_id, approved_by="operator", note="manual staging")

    empty_ctx = WrapperContext(policy_engine=PolicyEngine({}), journal=journal, dry_run=False)
    with pytest.raises(ValueError, match="missing execution contract"):
        spec.execute(empty_ctx, approved.op_id)


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_execute_approved_can_restamp_legacy_rows_when_policy_is_supplied(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(monkeypatch, calls)

    op = journal.begin(
        scope="test",
        kind=spec.kind,
        trust_zone="automation",
        normalized_target=spec.normalized_target,
        inputs=spec.payload,
    )
    approved = journal.approve(op.op_id, approved_by="operator", note="legacy staged row")

    executed = spec.execute(ctx, approved.op_id)

    assert executed["ok"] is True
    assert executed["executed"] is True
    assert executed["status"] == "succeeded"
    assert calls == ["request"]

    persisted = journal.get(approved.op_id)
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None


def test_json_http_client_retries_when_policy_allows(monkeypatch: MonkeyPatch) -> None:
    calls: list[str] = []

    def _request(*args: object, **kwargs: object) -> _FakeResponse:
        calls.append("request")
        if len(calls) == 1:
            raise requests.Timeout("transient timeout")
        return _FakeResponse(text="ok")

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)
    client = JsonHttpClient(timeout=5)

    outcome = client.post(
        "https://example.internal/hooks/deploy",
        headers={"Content-Type": "application/json"},
        json_body={"ok": True},
        retry_policy=RetryPolicy(
            name="safe-test",
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )

    assert outcome.request_attempts == 2
    assert outcome.request_method == "POST"
    assert outcome.request_url == "https://example.internal/hooks/deploy"
    assert outcome.response.text == "ok"
    assert calls == ["request", "request"]
