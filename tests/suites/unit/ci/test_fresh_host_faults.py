"""Unit coverage for the CI-only fresh-host fault-injection switch."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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


_HEALTH_FAULT = faults.ORBSTACK_STOP_DURING_SIDECARS_HEALTH
_COMPOSE_FILE = Path("docker-compose.aux-stack.ci-hosted-macos.yaml")


@pytest.mark.parametrize("fault", sorted(faults.KNOWN_FAULTS))
def test_every_fault_is_inert_on_push_to_main(fault: str) -> None:
    assert faults.requested_fault(_environ(fault, event="push", ref="refs/heads/main")) is None


def _entries(**health: str) -> dict[str, dict[str, object]]:
    """Return a running sidecars stack with per-service health overrides."""
    services: dict[str, dict[str, object]] = {
        "postgres": {"Service": "postgres", "State": "running", "Health": "healthy"},
        "litellm": {"Service": "litellm", "State": "running", "Health": "healthy"},
        "otel-collector": {"Service": "otel-collector", "State": "running"},
        "qdrant": {"Service": "qdrant", "State": "running", "Health": "healthy"},
    }
    for name, value in health.items():
        services[name]["Health"] = value
    return services


_WAITING = _entries(litellm="starting", qdrant="starting")
_PARTIAL = {"postgres": _entries()["postgres"], "litellm": _entries(litellm="starting")["litellm"]}


@pytest.mark.parametrize(
    ("services", "expected"),
    [
        (_WAITING, "litellm=starting, qdrant=starting"),
        (_entries(), None),
        (_PARTIAL, None),
        ({}, None),
    ],
)
def test_pending_sidecar_health_sees_only_a_started_stack_waiting_on_health(
    services: dict[str, dict[str, object]], expected: str | None
) -> None:
    assert faults.pending_sidecar_health(services) == expected


class _InstantEvent(threading.Event):
    """An event whose waits return at once, so polling loops run without real sleeps."""

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        return self.is_set()


def _script_compose_ps(
    test_context: TestContext,
    *outcomes: dict[str, dict[str, object]] | FreshHostError,
    on_poll: Callable[[], None] | None = None,
) -> list[int]:
    polls: list[int] = []
    remaining = list(outcomes)

    def fake_entries(compose_file: Path, **kwargs: object) -> dict[str, dict[str, object]]:
        del compose_file, kwargs
        polls.append(len(polls) + 1)
        if on_poll is not None:
            on_poll()
        outcome = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(outcome, FreshHostError):
            raise outcome
        return outcome

    test_context.patch.patch_object(faults, "compose_service_entries", new=fake_entries)
    return polls


def test_health_wait_watcher_stops_orbstack_on_the_second_sighting(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    commands = _record_commands(test_context)
    polls = _script_compose_ps(
        test_context, _PARTIAL, _WAITING, FreshHostError("blip"), _WAITING, _WAITING
    )

    cast(Any, faults)._stop_orbstack_in_health_wait(
        context, fault=_HEALTH_FAULT, compose_file=_COMPOSE_FILE, env={}, stop=_InstantEvent()
    )

    assert polls == [1, 2, 3, 4, 5]
    assert commands == [["orb", "stop"]]
    marker = json.loads(
        (Path(context.diagnostics_dir) / "fault-injection" / f"{_HEALTH_FAULT}.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["when"] == "inside the sidecars health wait: litellm=starting, qdrant=starting"


def test_health_wait_watcher_never_stops_orbstack_after_it_is_told_to_stop(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    commands = _record_commands(test_context)
    stop = _InstantEvent()
    polls = _script_compose_ps(test_context, _WAITING, on_poll=stop.set)

    cast(Any, faults)._stop_orbstack_in_health_wait(
        context, fault=_HEALTH_FAULT, compose_file=_COMPOSE_FILE, env={}, stop=stop
    )

    assert polls == [1]
    assert commands == []


def test_health_wait_fault_is_inert_unless_requested(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    commands = _record_commands(test_context)

    def unexpected_probe_env(*args: object, **kwargs: object) -> dict[str, str]:
        raise AssertionError((args, kwargs))

    test_context.patch.patch_object(faults, "compose_probe_env", new=unexpected_probe_env)

    for environ in (
        _environ(_HEALTH_FAULT, event="push", ref="refs/heads/main"),
        _environ(_FAULT, event="workflow_dispatch", ref=_BRANCH_REF),
        {},
    ):
        with faults.orbstack_stop_during_sidecars_health(
            context, compose_file=_COMPOSE_FILE, environ=environ
        ):
            pass

    assert commands == []
    assert not (Path(context.diagnostics_dir) / "fault-injection").exists()


def _arm_health_fault(test_context: TestContext, *, poll_seconds: float) -> threading.Event:
    """Stub the watcher's env and poll pace; return an event set when `orb stop` runs."""
    fired = threading.Event()

    def fake_run_command(command: list[str], **kwargs: object) -> None:
        del kwargs
        assert command == ["orb", "stop"]
        fired.set()

    def fake_probe_env(*args: object, **kwargs: object) -> dict[str, str]:
        del args, kwargs
        return {}

    test_context.patch.patch_object(faults, "run_command", new=fake_run_command)
    test_context.patch.patch_object(faults, "compose_probe_env", new=fake_probe_env)
    test_context.patch.patch_object(faults, "_HEALTH_WAIT_POLL_SECONDS", new=poll_seconds)
    return fired


def test_health_wait_fault_fires_once_while_sidecars_up_waits(
    tmp_path: Path, test_context: TestContext
) -> None:
    context = _macos_context(tmp_path, test_context)
    fired = _arm_health_fault(test_context, poll_seconds=0.0)
    _script_compose_ps(test_context, _WAITING)
    environ = _environ(_HEALTH_FAULT, event="workflow_dispatch", ref=_BRANCH_REF)

    with faults.orbstack_stop_during_sidecars_health(
        context, compose_file=_COMPOSE_FILE, environ=environ
    ):
        assert fired.wait(timeout=10)
    fired.clear()
    with faults.orbstack_stop_during_sidecars_health(
        context, compose_file=_COMPOSE_FILE, environ=environ
    ):
        pass

    assert not fired.is_set()
    assert (Path(context.diagnostics_dir) / "fault-injection" / f"{_HEALTH_FAULT}.json").is_file()


def test_health_wait_fault_reports_when_no_health_wait_was_seen(
    tmp_path: Path, test_context: TestContext, capsys: pytest.CaptureFixture[str]
) -> None:
    context = _macos_context(tmp_path, test_context)
    fired = _arm_health_fault(test_context, poll_seconds=0.01)
    _script_compose_ps(test_context, _entries())
    environ = _environ(_HEALTH_FAULT, event="workflow_dispatch", ref=_BRANCH_REF)

    with faults.orbstack_stop_during_sidecars_health(
        context, compose_file=_COMPOSE_FILE, environ=environ
    ):
        pass

    assert not fired.is_set()
    assert f"Fault injection {_HEALTH_FAULT} did not fire" in capsys.readouterr().out
