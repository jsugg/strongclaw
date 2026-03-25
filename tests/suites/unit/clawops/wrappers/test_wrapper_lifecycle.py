"""Policy and replay coverage for wrapper operations."""

from __future__ import annotations

import pathlib

import pytest
from pytest import MonkeyPatch

from clawops.common import write_yaml
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from tests.utils.helpers.wrappers import (
    SPECS,
    WrapperSpec,
    allow_decision_json,
    build_context,
    configure_wrapper_environment,
    expected_failure_attempts,
    expected_failure_retryable,
    install_success_response,
    install_transport_error,
)


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_denies_non_allowlisted_target(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False, dry_run=True)
    configure_wrapper_environment(spec, monkeypatch)

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
    ctx, journal = build_context(tmp_path, spec, require_approval=True)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

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
    ctx, journal = build_context(tmp_path, spec, require_approval=True)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

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
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

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
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_transport_error(monkeypatch, "simulated timeout", calls)

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is False
    assert first["accepted"] is True
    assert first["executed"] is True
    assert first["status"] == "failed"
    assert first["body"] == "simulated timeout"
    assert first["error_type"] == "timeout"
    assert first["retryable"] is expected_failure_retryable(spec)
    assert first["request_attempts"] == expected_failure_attempts(spec)
    assert first["error"]["type"] == "timeout"
    assert first["error"]["message"] == "simulated timeout"
    assert second == first
    assert calls == ["request"] * expected_failure_attempts(spec)

    persisted = journal.get(str(first["op_id"]))
    assert persisted.status == "failed"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
    assert persisted.result_error_type == "timeout"
    assert persisted.result_error_retryable == int(expected_failure_retryable(spec))
    assert persisted.result_request_method == (
        "PUT" if spec.kind == "github_pull_merge" else "POST"
    )
    assert persisted.result_request_attempts == expected_failure_attempts(spec)
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_transport_error_transitions_to_failed_terminal_state(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    install_transport_error(monkeypatch, "simulated timeout")

    result = spec.invoke(ctx, spec.allowed_input)

    assert result["ok"] is False
    assert result["accepted"] is True
    assert result["executed"] is True
    assert result["status"] == "failed"
    assert result["body"] == "simulated timeout"
    assert result["error_type"] == "timeout"
    assert result["retryable"] is expected_failure_retryable(spec)
    assert result["request_method"] == ("PUT" if spec.kind == "github_pull_merge" else "POST")
    assert result["request_attempts"] == expected_failure_attempts(spec)
    assert result["error"]["type"] == "timeout"
    assert result["error"]["message"] == "simulated timeout"

    persisted = journal.get(str(result["op_id"]))
    assert persisted.status == "failed"
    assert persisted.last_error == "simulated timeout"
    assert persisted.result_ok == 0
    assert persisted.result_body_excerpt == "simulated timeout"
    assert persisted.result_error_type == "timeout"
    assert persisted.result_error_retryable == int(expected_failure_retryable(spec))
    assert persisted.result_request_method == (
        "PUT" if spec.kind == "github_pull_merge" else "POST"
    )
    assert persisted.result_request_attempts == expected_failure_attempts(spec)
    assert persisted.attempt == 1
    assert journal.list_stuck(older_than_ms=0) == []
    assert persisted.execution_contract_version == 1


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_running_operation_without_duplicate_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

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
        policy_decision_json=allow_decision_json(),
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
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

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
    configure_wrapper_environment(spec, monkeypatch)

    replayed = spec.invoke(deny_ctx, spec.allowed_input)

    assert first["status"] == "succeeded"
    assert replayed["status"] == "succeeded"
    assert replayed["decision"]["decision"] == "allow"
    assert replayed["decision"] == first["decision"]
    assert calls == ["request"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.policy_decision == "allow"
    assert persisted.execution_contract_version == 1
