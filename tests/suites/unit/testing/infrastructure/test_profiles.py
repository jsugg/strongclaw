"""Unit coverage for infrastructure test profiles and environment helpers."""

from __future__ import annotations

from tests.plugins.infrastructure.context import TestContext
from tests.plugins.infrastructure.environment import EnvironmentManager
from tests.plugins.infrastructure.profiles import available_profiles, resolve_profile


def test_available_profiles_include_core_runtime_profiles() -> None:
    assert {
        "fresh_host_push",
        "model_setup_skip",
        "retry_safe",
        "structured_logs",
        "workflow_state",
    }.issubset(set(available_profiles()))


def test_workflow_state_profile_requires_override() -> None:
    profile = resolve_profile("workflow_state")

    try:
        profile.resolve_env()
    except ValueError as exc:
        assert "STRONGCLAW_STATE_DIR" in str(exc)
    else:  # pragma: no cover - defensive guard.
        raise AssertionError("workflow_state profile must require STRONGCLAW_STATE_DIR")


def test_environment_manager_applies_profile_overrides() -> None:
    manager = EnvironmentManager()
    manager.snapshot()
    manager.apply_profile(
        "workflow_state",
        overrides={"STRONGCLAW_STATE_DIR": "/tmp/state"},
    )

    assert (
        resolve_profile("workflow_state").resolve_env(
            overrides={"STRONGCLAW_STATE_DIR": "/tmp/state"}
        )["STRONGCLAW_STATE_DIR"]
        == "/tmp/state"
    )
    manager.restore()


def test_test_context_apply_profiles_uses_bound_environment() -> None:
    context = TestContext()
    manager = EnvironmentManager()
    manager.snapshot()
    context.attach_environment(manager)

    context.apply_profiles("structured_logs", "fresh_host_push")

    assert manager is context.env
    manager.restore()
