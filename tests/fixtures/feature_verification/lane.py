"""Pytest hooks and fixtures for the end-to-end feature-verification suite.

Outside a provisioned lane (``FEATURE_VERIFICATION_LANE`` unset) the suite is deselected, so
``make test`` and nested product runs such as ``clawops baseline verify`` never touch it.
Inside a lane, only the features the matrix schedules on that lane run, and every outcome is
written to the lane results file the reporter joins with the matrix.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

import pytest

from tests.utils.helpers.feature_verification import (
    FeatureHost,
    GatewayController,
    LaneRecorder,
    is_feature_suite_item,
)

_RECORDER_KEY: Final[pytest.StashKey[LaneRecorder]] = pytest.StashKey[LaneRecorder]()
# The logreport hook receives no config, so the active recorder is also held here.
_ACTIVE_RECORDERS: Final[dict[str, LaneRecorder]] = {}


def pytest_configure(config: pytest.Config) -> None:
    """Attach the lane recorder when running inside a provisioned lane."""
    recorder = LaneRecorder.from_environment()
    if recorder is not None:
        config.stash[_RECORDER_KEY] = recorder
        _ACTIVE_RECORDERS["lane"] = recorder


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the suite outside a lane, and features scheduled on other lanes inside one."""
    suite_items = [item for item in items if is_feature_suite_item(item)]
    if not suite_items:
        return
    recorder = config.stash.get(_RECORDER_KEY, None)
    if recorder is None:
        deselected = suite_items
    else:
        _, deselected = recorder.select(suite_items)
    if deselected:
        dropped = {id(item) for item in deselected}
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if id(item) not in dropped]


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record feature outcomes as phases finish."""
    recorder = _ACTIVE_RECORDERS.get("lane")
    if recorder is not None:
        recorder.record(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Persist the lane results file."""
    del exitstatus
    recorder = session.config.stash.get(_RECORDER_KEY, None)
    if recorder is not None:
        recorder.write()
        _ACTIVE_RECORDERS.pop("lane", None)


@pytest.fixture(scope="session")
def feature_host(request: pytest.FixtureRequest) -> FeatureHost:
    """Return the provisioned host this lane verifies, recording tool versions once."""
    host = FeatureHost.from_environment()
    recorder = request.config.stash.get(_RECORDER_KEY, None)
    if recorder is not None and not recorder.versions:
        for tool in ("openclaw", "acpx"):
            outcome = host.run([tool, "--version"], timeout=60)
            recorder.versions[tool] = (
                outcome.stdout.strip().splitlines()[-1]
                if outcome.ok and outcome.stdout.strip()
                else f"unavailable (exit {outcome.returncode})"
            )
    return host


@pytest.fixture(scope="session")
def gateway(feature_host: FeatureHost) -> Iterator[GatewayController]:
    """Return a running gateway, started by the harness when no service supervises one."""
    controller = GatewayController(feature_host)
    controller.ensure_running()
    try:
        yield controller
    finally:
        controller.stop()
