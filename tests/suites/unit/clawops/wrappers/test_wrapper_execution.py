"""Execution-path coverage for policy-gated wrappers."""

from __future__ import annotations

import pathlib

import pytest

from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.wrappers import (
    SPECS,
    WrapperSpec,
    build_context,
    configure_wrapper_environment,
)
from tests.utils.helpers.wrappers_http import install_success_response


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_wrapper_requires_explicit_approval_then_replays_terminal_result(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=True)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_success_response(test_context, calls)

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
def test_execute_approved_rejects_manual_rows_without_execution_contract(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    _ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
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
    approved = journal.approve(op.op_id, approved_by="operator", note="legacy staged row")

    executed = spec.execute(ctx, approved.op_id)

    assert executed["ok"] is True
    assert executed["executed"] is True
    assert executed["status"] == "succeeded"
    assert calls == ["request"]

    persisted = journal.get(approved.op_id)
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None
