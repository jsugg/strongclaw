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
from clawops.wrappers.base import WrapperContext
from clawops.wrappers.github import create_comment, execute_github_comment_approved
from clawops.wrappers.jira import add_comment, execute_jira_comment_approved
from clawops.wrappers.webhook import execute_webhook_approved, invoke_webhook

type InvokeWrapper = Callable[[WrapperContext, str], dict[str, object]]
type ExecuteWrapper = Callable[[WrapperContext, str], dict[str, object]]
type AllowlistValue = Callable[[str], str]


@dataclasses.dataclass(frozen=True, slots=True)
class WrapperSpec:
    """Wrapper-specific lifecycle contract inputs."""

    name: str
    action: str
    allowlist_key: str
    allowed_input: str
    denied_input: str
    invoke: InvokeWrapper
    execute: ExecuteWrapper
    patch_target: str
    allowlist_value: AllowlistValue
    env: dict[str, str] = dataclasses.field(default_factory=dict)


class _FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, text: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text


def _identity(value: str) -> str:
    return value


def _jira_project(issue_key: str) -> str:
    return issue_key.split("-", 1)[0]


def _invoke_webhook(ctx: WrapperContext, url: str) -> dict[str, object]:
    return invoke_webhook(
        ctx=ctx,
        url=url,
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )


def _invoke_github(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return create_comment(
        ctx=ctx,
        repo=repo,
        issue_number=123,
        body="hello",
        scope="test",
        trust_zone="automation",
    )


def _invoke_jira(ctx: WrapperContext, issue_key: str) -> dict[str, object]:
    return add_comment(
        ctx=ctx,
        issue_key=issue_key,
        body="hello",
        scope="test",
        trust_zone="automation",
    )


def _execute_webhook(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_webhook_approved(ctx=ctx, op_id=op_id)


def _execute_github(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_github_comment_approved(ctx=ctx, op_id=op_id)


def _execute_jira(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_jira_comment_approved(ctx=ctx, op_id=op_id)


SPECS = (
    WrapperSpec(
        name="webhook",
        action="webhook.post",
        allowlist_key="webhook_url",
        allowed_input="https://example.internal/hooks/deploy",
        denied_input="https://evil.invalid/hooks/deploy",
        invoke=_invoke_webhook,
        execute=_execute_webhook,
        patch_target="clawops.wrappers.base.requests.post",
        allowlist_value=_identity,
    ),
    WrapperSpec(
        name="github",
        action="github.comment.create",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        invoke=_invoke_github,
        execute=_execute_github,
        patch_target="clawops.wrappers.base.requests.post",
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
    WrapperSpec(
        name="jira",
        action="jira.comment.create",
        allowlist_key="jira_project",
        allowed_input="OPS-123",
        denied_input="BAD-123",
        invoke=_invoke_jira,
        execute=_execute_jira,
        patch_target="clawops.wrappers.jira.requests.post",
        allowlist_value=_jira_project,
        env={
            "JIRA_BASE_URL": "https://jira.example.internal",
            "JIRA_EMAIL": "operator@example.internal",
            "JIRA_API_TOKEN": "test-token",
        },
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
                "allow_categories": ["external_write"],
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


def _install_success_response(
    spec: WrapperSpec,
    monkeypatch: MonkeyPatch,
    calls: list[str],
) -> None:
    def _post(*args: object, **kwargs: object) -> _FakeResponse:
        calls.append("post")
        return _FakeResponse(text="ok")

    monkeypatch.setattr(spec.patch_target, _post)


def _install_transport_error(
    spec: WrapperSpec,
    monkeypatch: MonkeyPatch,
    message: str,
    calls: list[str] | None = None,
) -> None:
    def _post(*args: object, **kwargs: object) -> _FakeResponse:
        if calls is not None:
            calls.append("post")
        raise requests.Timeout(message)

    monkeypatch.setattr(spec.patch_target, _post)


def _normalized_target_for_spec(spec: WrapperSpec) -> str:
    if spec.name == "webhook":
        return spec.allowed_input
    if spec.name == "github":
        return f"github://{spec.allowed_input}/issues/123"
    if spec.name == "jira":
        return spec.allowed_input
    raise AssertionError(f"unknown wrapper spec: {spec.name}")


def _kind_for_spec(spec: WrapperSpec) -> str:
    if spec.name == "webhook":
        return "webhook_post"
    if spec.name == "github":
        return "github_comment"
    if spec.name == "jira":
        return "jira_comment"
    raise AssertionError(f"unknown wrapper spec: {spec.name}")


def _payload_for_spec(spec: WrapperSpec) -> dict[str, object]:
    if spec.name == "webhook":
        return {"ok": True}
    if spec.name in {"github", "jira"}:
        return {"body": "hello"}
    raise AssertionError(f"unknown wrapper spec: {spec.name}")


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
    _install_success_response(spec, monkeypatch, calls)

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
    assert calls == ["post"]

    persisted = journal.get(str(prepared["op_id"]))
    assert persisted.approved_by == "operator"
    assert persisted.result_ok == 1
    assert persisted.result_status_code == 200
    assert persisted.result_body_excerpt == "ok"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_pending_approval_without_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=True)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(spec, monkeypatch, calls)

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


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_success_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_success_response(spec, monkeypatch, calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is True
    assert first["executed"] is True
    assert first["status"] == "succeeded"
    assert second["ok"] is True
    assert second["executed"] is True
    assert second["status"] == "succeeded"
    assert second["body"] == "ok"
    assert calls == ["post"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.result_ok == 1
    assert persisted.result_status_code == 200
    assert persisted.result_body_excerpt == "ok"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_failed_terminal_result_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = _build_context(tmp_path, spec, require_approval=False)
    _configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    _install_transport_error(spec, monkeypatch, "simulated timeout", calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is False
    assert first["accepted"] is True
    assert first["executed"] is True
    assert first["status"] == "failed"
    assert first["body"] == "simulated timeout"
    assert second == first
    assert calls == ["post"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.status == "failed"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
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
    _install_transport_error(spec, monkeypatch, "simulated timeout")

    result = spec.invoke(ctx, spec.allowed_input)

    assert result["ok"] is False
    assert result["accepted"] is True
    assert result["executed"] is True
    assert result["status"] == "failed"
    assert result["body"] == "simulated timeout"

    persisted = journal.get(str(result["op_id"]))
    assert persisted.status == "failed"
    assert persisted.last_error == "simulated timeout"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
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
    _install_success_response(spec, monkeypatch, calls)

    op = journal.begin(
        scope="test",
        kind=_kind_for_spec(spec),
        trust_zone="automation",
        normalized_target=_normalized_target_for_spec(spec),
        inputs=_payload_for_spec(spec),
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
    _install_success_response(spec, monkeypatch, calls)

    first = spec.invoke(ctx, spec.allowed_input)

    deny_policy_path = tmp_path / f"{spec.name}-deny-policy.yaml"
    write_yaml(
        deny_policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "automation": {
                    "allow_actions": [spec.action],
                    "allow_categories": ["external_write"],
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
    assert calls == ["post"]

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
        kind=_kind_for_spec(spec),
        trust_zone="automation",
        normalized_target=_normalized_target_for_spec(spec),
        inputs=_payload_for_spec(spec),
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
    _install_success_response(spec, monkeypatch, calls)

    op = journal.begin(
        scope="test",
        kind=_kind_for_spec(spec),
        trust_zone="automation",
        normalized_target=_normalized_target_for_spec(spec),
        inputs=_payload_for_spec(spec),
    )
    approved = journal.approve(op.op_id, approved_by="operator", note="legacy staged row")

    executed = spec.execute(ctx, approved.op_id)

    assert executed["ok"] is True
    assert executed["executed"] is True
    assert executed["status"] == "succeeded"
    assert calls == ["post"]

    persisted = journal.get(approved.op_id)
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None
