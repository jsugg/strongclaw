"""Lifecycle and replay coverage for policy-gated wrappers."""

from __future__ import annotations

import pathlib

import pytest

from clawops.typed_values import as_mapping
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.wrappers import (
    SPECS,
    WrapperSpec,
    allow_decision_json,
    build_context,
    configure_wrapper_environment,
    expected_failure_attempts,
    expected_failure_retryable,
)
from tests.utils.helpers.wrappers_http import install_success_response, install_transport_error


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_replays_pending_approval_without_side_effect(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=True)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_success_response(test_context, calls)

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
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_success_response(test_context, calls)

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
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_transport_error(test_context, "simulated timeout", calls)

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
    first_error = as_mapping(first["error"], path="first.error")
    assert first_error["type"] == "timeout"
    assert first_error["message"] == "simulated timeout"
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
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
    install_transport_error(test_context, "simulated timeout")

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
    result_error = as_mapping(result["error"], path="result.error")
    assert result_error["type"] == "timeout"
    assert result_error["message"] == "simulated timeout"

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
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_success_response(test_context, calls)

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
    replayed_decision = as_mapping(replayed["decision"], path="replayed.decision")
    assert replayed_decision["decision"] == "allow"
    assert calls == []

    persisted = journal.get(str(replayed["op_id"]))
    assert persisted.status == "running"
    assert persisted.attempt == 1
    assert persisted.execution_contract_version == 1
