"""Unit coverage for the fresh-host job budget and phase deadlines."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import fresh_host as fresh_host_script
from tests.utils.helpers._fresh_host.budget import (
    IMAGE_PHASE_CAP_SECONDS,
    IMAGE_PHASE_MIN_SECONDS,
    JOB_DEADLINE_ENV,
    JOB_START_SKEW_SECONDS,
    JOB_STARTED_ENV,
    OBSERVER_RESERVE_ENV,
    RUNTIME_PHASE_CAP_SECONDS,
    SCENARIO_RESERVE_ENV,
    BudgetExhaustedError,
    JobBudget,
    PhaseDeadline,
    image_phase_deadline,
    initialize_job_budget,
    phase_deadline,
    record_job_budget,
    runtime_phase_deadline,
    scenario_phase_deadline,
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


def test_image_phase_deadline_uses_phase_cap_when_reserves_allow() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(4 * 60)

    deadline = image_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "phase-cap"
    assert deadline.remaining(clock) == IMAGE_PHASE_CAP_SECONDS


def test_image_phase_deadline_protects_scenario_and_observer_reserves() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(20 * 60)

    deadline = image_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "job-reserve"
    reserve_limit = budget.deadline_epoch - (35 + 15) * 60
    assert deadline.wall_deadline_epoch == reserve_limit
    assert deadline.remaining(clock) == pytest.approx(reserve_limit - clock.wall)


def test_image_phase_deadline_fails_fast_once_reserves_are_reached() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(40 * 60)

    with pytest.raises(BudgetExhaustedError, match="^budget_exhausted"):
        image_phase_deadline(budget, clock=clock)


def test_phase_deadline_without_budget_is_capped() -> None:
    clock = FakeClock()

    deadline = phase_deadline(None, phase="test", phase_cap_seconds=90, clock=clock)

    assert deadline.limited_by == "phase-cap"
    assert deadline.remaining(clock) == 90


def test_runtime_phase_deadline_uses_its_cap_when_the_job_has_room() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(2 * 60)

    deadline = runtime_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "phase-cap"
    assert deadline.remaining(clock) == RUNTIME_PHASE_CAP_SECONDS


def test_runtime_phase_deadline_reserves_the_image_phase_minimum() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(5 * 60)

    deadline = runtime_phase_deadline(budget, clock=clock)

    assert deadline.limited_by == "job-reserve"
    image_floor = budget.deadline_epoch - (35 + 15) * 60 - IMAGE_PHASE_MIN_SECONDS
    assert deadline.wall_deadline_epoch == image_floor
    assert deadline.remaining(clock) == pytest.approx(540)


def test_runtime_phase_deadline_fails_fast_when_later_reserves_consume_the_budget() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(15 * 60)

    with pytest.raises(BudgetExhaustedError, match="^budget_exhausted: no runtime time"):
        runtime_phase_deadline(budget, clock=clock)


@pytest.mark.parametrize(
    ("runtime_spend", "expected_image_seconds", "limited_by"),
    [(1.0, IMAGE_PHASE_CAP_SECONDS, "phase-cap"), (None, IMAGE_PHASE_MIN_SECONDS, "job-reserve")],
)
def test_image_phase_consumes_what_the_runtime_phase_left(
    runtime_spend: float | None, expected_image_seconds: int, limited_by: str
) -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(5 * 60)
    runtime = runtime_phase_deadline(budget, clock=clock)
    clock.advance(runtime.remaining(clock) if runtime_spend is None else runtime_spend)

    image = image_phase_deadline(budget, clock=clock)

    assert image.limited_by == limited_by
    assert image.remaining(clock) == pytest.approx(expected_image_seconds)


def test_phase_deadline_slice_never_outlives_its_parent() -> None:
    clock = FakeClock()
    parent = PhaseDeadline.after(300, clock=clock, limited_by="job-reserve")

    capped = parent.slice(clock, cap_seconds=100, limited_by="recovery-cap")
    reserved = parent.slice(clock, cap_seconds=1000, reserve_seconds=60, limited_by="unused")
    overdrawn = parent.slice(clock, reserve_seconds=400)

    assert (capped.remaining(clock), capped.limited_by) == (100, "recovery-cap")
    assert (reserved.remaining(clock), reserved.limited_by) == (240, "job-reserve")
    assert reserved.wall_deadline_epoch == parent.wall_deadline_epoch - 60
    assert overdrawn.remaining(clock) == 0.0


def test_phase_deadline_command_timeout_refuses_to_start_without_time() -> None:
    clock = FakeClock()
    deadline = PhaseDeadline.after(30, clock=clock)

    assert deadline.command_timeout(120, clock) == 30
    clock.advance(29.5)
    with pytest.raises(BudgetExhaustedError, match="^budget_exhausted"):
        deadline.command_timeout(120, clock)


def test_phase_deadline_follows_monotonic_time_after_wall_clock_jumps() -> None:
    clock = FakeClock()
    deadline = PhaseDeadline.after(300, clock=clock)

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


def test_scenario_phase_deadline_follows_the_scenario_step_timeout_when_the_job_has_room() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(600)

    deadline = scenario_phase_deadline(budget, clock=clock)

    assert deadline.remaining(clock) == budget.scenario_reserve_seconds
    assert deadline.limited_by == "phase-cap"


def test_scenario_phase_deadline_keeps_the_observer_reserve_when_the_scenario_starts_late() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    observer_limit = budget.deadline_epoch - budget.observer_reserve_seconds
    clock.advance(observer_limit - clock.time() - 300)

    deadline = scenario_phase_deadline(budget, clock=clock)

    assert deadline.wall_deadline_epoch == observer_limit
    assert deadline.remaining(clock) == 300
    assert deadline.limited_by == "job-reserve"


def test_scenario_phase_deadline_fails_fast_inside_the_observer_reserve() -> None:
    clock = FakeClock()
    budget = _macos_budget(clock)
    clock.advance(budget.deadline_epoch - budget.observer_reserve_seconds - clock.time())

    with pytest.raises(BudgetExhaustedError, match="no scenario time remains"):
        scenario_phase_deadline(budget, clock=clock)
