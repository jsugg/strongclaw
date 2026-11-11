"""Unit coverage for the CI-only fresh-host fault-injection switch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import faults
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError

_FAULT = faults.ORBSTACK_STOP_DURING_SIDECARS
_BRANCH_REF = "refs/heads/fix/runtime-loss"


def _environ(fault: str, *, event: str, ref: str) -> dict[str, str]:
    return {faults.FAULT_INJECTION_ENV: fault, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref}


@pytest.mark.parametrize(
    ("event", "ref"),
    [
        ("push", "refs/heads/main"),
        ("push", _BRANCH_REF),
        ("pull_request", "refs/pull/252/merge"),
        ("schedule", "refs/heads/main"),
        ("workflow_dispatch", "refs/heads/main"),
        ("", ""),
    ],
)
def test_fault_requests_are_ignored_outside_manual_dispatch_off_main(event: str, ref: str) -> None:
    assert faults.requested_fault(_environ(_FAULT, event=event, ref=ref)) is None


def test_fault_requests_are_honored_on_manual_dispatch_off_main() -> None:
    environ = _environ(_FAULT, event="workflow_dispatch", ref=_BRANCH_REF)

    assert faults.requested_fault(environ) == _FAULT


@pytest.mark.parametrize("value", ["", "  ", faults.FAULT_INJECTION_OFF])
def test_unset_or_off_fault_requests_inject_nothing(value: str) -> None:
    environ = _environ(value, event="workflow_dispatch", ref=_BRANCH_REF)

    assert faults.requested_fault(environ) is None


def test_unknown_fault_requests_fail_loudly() -> None:
    environ = _environ("kill-everything", event="workflow_dispatch", ref=_BRANCH_REF)

    with pytest.raises(FreshHostError, match="is not one of"):
        faults.requested_fault(environ)


def _macos_context(tmp_path: Path, test_context: TestContext) -> FreshHostContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.apply_profiles("fresh_host_macos_orbstack")
    return fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=tmp_path / "github.env",
    )


def _record_commands(test_context: TestContext) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], **kwargs: object) -> None:
        del kwargs
        commands.append(list(command))

    test_context.patch.patch_object(faults, "run_command", new=fake_run_command)
    return commands


def test_sidecars_runtime_loss_fires_orb_stop_exactly_once(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    commands = _record_commands(test_context)
    environ = _environ(_FAULT, event="workflow_dispatch", ref=_BRANCH_REF)

    first = faults.inject_sidecars_runtime_loss(context, environ=environ)
    second = faults.inject_sidecars_runtime_loss(context, environ=environ)

    assert (first, second) == (True, False)
    assert commands == [["orb", "stop"]]
    marker = Path(context.diagnostics_dir) / "fault-injection" / f"{_FAULT}.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["fault"] == _FAULT


def test_sidecars_runtime_loss_is_inert_on_push_to_main(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    commands = _record_commands(test_context)

    fired = faults.inject_sidecars_runtime_loss(
        context, environ=_environ(_FAULT, event="push", ref="refs/heads/main")
    )

    assert fired is False
    assert commands == []
    assert not (Path(context.diagnostics_dir) / "fault-injection").exists()
