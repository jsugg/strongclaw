"""Contracts for framework-managed environment isolation."""

from __future__ import annotations

import os

import pytest

from tests.plugins.infrastructure import TestContext
from tests.utils.helpers.env import FRAMEWORK_ENV_VARS, EnvironmentManager


def test_isolated_mode_restores_full_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASELINE_ONLY", "before")
    manager = EnvironmentManager(mode="isolated")
    manager.snapshot()
    manager.inject(TEST_ID="tid", RESOURCE_PREFIX="prefix", WORKER_ID="main")
    os.environ["BASELINE_ONLY"] = "after"
    os.environ["EXTRA_ONLY"] = "value"

    manager.restore()

    assert os.environ["BASELINE_ONLY"] == "before"
    assert "EXTRA_ONLY" not in os.environ


def test_shared_mode_restores_only_injected_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASELINE_ONLY", "before")
    manager = EnvironmentManager(mode="shared")
    manager.snapshot()
    manager.inject(RUNTIME_ONLY="tid")
    os.environ["BASELINE_ONLY"] = "after"

    manager.restore()

    assert os.environ["BASELINE_ONLY"] == "after"
    assert "RUNTIME_ONLY" not in os.environ


def test_framework_vars_are_injected_during_test() -> None:
    manager = EnvironmentManager(mode="isolated")
    manager.snapshot()
    manager.inject(TEST_ID="tid", RESOURCE_PREFIX="prefix", WORKER_ID="worker")

    assert all(os.environ[key] for key in FRAMEWORK_ENV_VARS)

    manager.restore()


def test_framework_vars_are_removed_after_test(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in FRAMEWORK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    manager = EnvironmentManager(mode="isolated")
    manager.snapshot()
    manager.inject(TEST_ID="tid", RESOURCE_PREFIX="prefix", WORKER_ID="worker")

    manager.restore()

    assert not any(key in os.environ for key in FRAMEWORK_ENV_VARS)


def test_universal_test_context_matches_framework_env(test_context: TestContext) -> None:
    assert os.environ["TEST_ID"] == test_context.tid
    assert os.environ["RESOURCE_PREFIX"] == test_context.resource_prefix
    assert os.environ["WORKER_ID"] == test_context.worker_id
