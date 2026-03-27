"""Policy-boundary coverage for policy-gated wrappers."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import write_yaml
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
def test_wrapper_denies_non_allowlisted_target(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False, dry_run=True)
    configure_wrapper_environment(spec, test_context)

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
def test_wrapper_replays_stored_decision_when_policy_changes(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, test_context)
    calls: list[str] = []
    install_success_response(test_context, calls)

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
    configure_wrapper_environment(spec, test_context)

    replayed = spec.invoke(deny_ctx, spec.allowed_input)

    assert first["status"] == "succeeded"
    assert replayed["status"] == "succeeded"
    assert replayed["decision"]["decision"] == "allow"
    assert replayed["decision"] == first["decision"]
    assert calls == ["request"]

    persisted = journal.get(str(first["op_id"]))
    assert persisted.policy_decision == "allow"
    assert persisted.execution_contract_version == 1
