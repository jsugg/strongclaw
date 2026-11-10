"""Unit coverage for the fresh-host job budget and phase deadlines."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import fresh_host as fresh_host_script
from tests.utils.helpers._fresh_host.budget import (
    JOB_DEADLINE_ENV,
    JOB_START_SKEW_SECONDS,
    JOB_STARTED_ENV,
    OBSERVER_RESERVE_ENV,
    PULL_PHASE_CAP_SECONDS,
    SCENARIO_RESERVE_ENV,
    JobBudget,
    initialize_job_budget,
    pull_phase_deadline,
    record_job_budget,
)
from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers.clock import FakeClock


def _macos_budget(clock: FakeClock) -> JobBudget:
    return initialize_job_budget(
        job_timeout_minutes=90,
        scenario_reserve_minutes=35,
        observer_reserve_minutes=15,
        environ={},
        clock=clock,
    )


def test_initialize_job_budget_records_absolute_deadline_inside_job_cap() -> None:
    clock = FakeClock()

    budget = _macos_budget(clock)

    assert budget.started_epoch == clock.wall
    assert budget.deadline_epoch == clock.wall + 90 * 60 - JOB_START_SKEW_SECONDS
    assert budget.scenario_reserve_seconds == 35 * 60
    assert budget.observer_reserve_seconds == 15 * 60


def test_initialize_job_budget_never_resets_a_recorded_budget() -> None:
    clock = FakeClock()
    recorded = _macos_budget(clock)
    clock.advance(600)

    again = initialize_job_budget(
        job_timeout_minutes=90,
        scenario_reserve_minutes=35,
        observer_reserve_minutes=15,
        environ=recorded.to_env(),
        clock=clock,
    )

    assert again == JobBudget.from_env(recorded.to_env())
    assert again.deadline_epoch == pytest.approx(recorded.deadline_epoch, abs=1e-3)


@pytest.mark.parametrize(
    ("job", "scenario", "observers"),
    [(90, 70, 20), (10, 5, 5), (0, 1, 1), (90, -1, 15)],
)
def test_initialize_job_budget_rejects_reserves_that_leave_no_pull_time(
    job: int, scenario: int, observers: int
) -> None:
    with pytest.raises(FreshHostError):
        initialize_job_budget(
            job_timeout_minutes=job,
            scenario_reserve_minutes=scenario,
            observer_reserve_minutes=observers,
            environ={},
            clock=FakeClock(),
        )


def test_job_budget_from_env_is_absent_for_local_runs() -> None:
    assert JobBudget.from_env({"UNRELATED": "1"}) is None


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({JOB_DEADLINE_ENV: "10"}, "incomplete"),
        (
            {
                JOB_STARTED_ENV: "10",
                JOB_DEADLINE_ENV: "soon",
                SCENARIO_RESERVE_ENV: "1",
                OBSERVER_RESERVE_ENV: "1",
            },
            "numeric",
        ),
        (
            {
                JOB_STARTED_ENV: "10",
                JOB_DEADLINE_ENV: "20",
                SCENARIO_RESERVE_ENV: "-5",
                OBSERVER_RESERVE_ENV: "1",
            },
            "negative",
        ),
        (
            {
                JOB_STARTED_ENV: "20",
                JOB_DEADLINE_ENV: "10",
                SCENARIO_RESERVE_ENV: "1",
                OBSERVER_RESERVE_ENV: "1",
            },
            "precedes",
        ),
    ],
)
def test_job_budget_from_env_rejects_invalid_records(environ: dict[str, str], message: str) -> None:
    with pytest.raises(FreshHostError, match=message):
        JobBudget.from_env(environ)


def test_pull_phase_deadline_uses_phase_cap_when_reserves_allow() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(4 * 60)

    deadline = pull_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "phase-cap"
    assert deadline.remaining(clock) == PULL_PHASE_CAP_SECONDS


def test_pull_phase_deadline_protects_scenario_and_observer_reserves() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(20 * 60)

    deadline = pull_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "job-reserve"
    reserve_limit = budget.deadline_epoch - (35 + 15) * 60
    assert deadline.wall_deadline_epoch == reserve_limit
    assert deadline.remaining(clock) == pytest.approx(reserve_limit - clock.wall)


def test_pull_phase_deadline_fails_fast_once_reserves_are_reached() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(40 * 60)

    with pytest.raises(FreshHostError, match="^budget_exhausted"):
        pull_phase_deadline(budget, clock=clock)


def test_pull_phase_deadline_without_budget_is_capped() -> None:
    clock = FakeClock()

    deadline = pull_phase_deadline(None, clock=clock, phase_cap_seconds=90)

    assert deadline.limited_by == "phase-cap"
    assert deadline.remaining(clock) == 90


def test_phase_deadline_follows_monotonic_time_after_wall_clock_jumps() -> None:
    clock = FakeClock()
    deadline = pull_phase_deadline(None, clock=clock, phase_cap_seconds=300)

    clock.wall -= 3600
    clock.mono += 100

    assert deadline.remaining(clock) == 200
    clock.mono += 1000
    assert deadline.remaining(clock) == 0.0


def test_record_job_budget_exports_github_env(tmp_path: Path) -> None:
    github_env = tmp_path / "github.env"
    clock = FakeClock()

    budget = record_job_budget(
        job_timeout_minutes=75,
        scenario_reserve_minutes=20,
        observer_reserve_minutes=9,
        environ={},
        github_env_file=github_env,
        clock=clock,
    )

    exported = dict(
        line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines()
    )
    assert JobBudget.from_env(exported) == JobBudget.from_env(budget.to_env())
    assert exported[SCENARIO_RESERVE_ENV] == "1200"
    assert exported[OBSERVER_RESERVE_ENV] == "540"


def test_initialize_budget_cli_writes_env_once(tmp_path: Path, test_context: TestContext) -> None:
    github_env = tmp_path / "github.env"
    for key in (JOB_STARTED_ENV, JOB_DEADLINE_ENV, SCENARIO_RESERVE_ENV, OBSERVER_RESERVE_ENV):
        test_context.env.remove(key)
    argv = [
        "initialize-budget",
        "--job-timeout-minutes",
        "90",
        "--scenario-reserve-minutes",
        "35",
        "--observer-reserve-minutes",
        "15",
        "--github-env-file",
        str(github_env),
    ]

    assert fresh_host_script.main(argv) == 0
    exported = dict(
        line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines()
    )
    assert JobBudget.from_env(exported) is not None
    assert fresh_host_script.main([*argv[:2], "5", *argv[3:]]) == 1
