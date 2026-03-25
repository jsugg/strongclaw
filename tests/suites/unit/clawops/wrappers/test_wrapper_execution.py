"""Execution-contract coverage for wrapper approval replays."""

from __future__ import annotations

import pathlib

import pytest
from pytest import MonkeyPatch

from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from tests.utils.helpers.wrappers import (
    SPECS,
    WrapperSpec,
    build_context,
    configure_wrapper_environment,
    install_success_response,
)


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_execute_approved_rejects_manual_rows_without_execution_contract(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
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
    approved = journal.approve(op.op_id, approved_by="operator", note="legacy staged row")

    executed = spec.execute(ctx, approved.op_id)

    assert executed["ok"] is True
    assert executed["executed"] is True
    assert executed["status"] == "succeeded"
    assert calls == ["request"]

    persisted = journal.get(approved.op_id)
    assert persisted.execution_contract_version == 1
    assert persisted.execution_contract_json is not None
