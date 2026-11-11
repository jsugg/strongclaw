"""Non-resettable job budget and phase deadlines for hosted fresh-host CI jobs.

GitHub exposes a job's `timeout-minutes` cap but not its remaining time, so the budget is
recorded once, right after checkout, as an absolute wall-clock deadline exported through
`GITHUB_ENV`. Every later phase derives its own deadline from that record and reserves the
time later phases need, so a slow phase fails fast instead of starving the scenario and
the best-effort observers that upload evidence.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._fresh_host.storage import write_github_env

JOB_STARTED_ENV: Final[str] = "FRESH_HOST_JOB_STARTED_EPOCH"
JOB_DEADLINE_ENV: Final[str] = "FRESH_HOST_JOB_DEADLINE_EPOCH"
SCENARIO_RESERVE_ENV: Final[str] = "FRESH_HOST_SCENARIO_RESERVE_SECONDS"
OBSERVER_RESERVE_ENV: Final[str] = "FRESH_HOST_OBSERVER_RESERVE_SECONDS"
_BUDGET_ENV_KEYS: Final[tuple[str, ...]] = (
    JOB_STARTED_ENV,
    JOB_DEADLINE_ENV,
    SCENARIO_RESERVE_ENV,
    OBSERVER_RESERVE_ENV,
)
# Runner setup and checkout elapse before the budget is recorded; keep the deadline inside
# the GitHub cap by that much.
JOB_START_SKEW_SECONDS: Final[int] = 120
# Time a phase helper needs after its deadline to terminate children and flush reports; each
# phase step's `timeout-minutes` backstop must cover its cap plus this margin.
PHASE_FLUSH_SECONDS: Final[int] = 120
IMAGE_PHASE_CAP_SECONDS: Final[int] = 1800
# The runtime phase leaves at least this much for images. Hosted macOS image phases took up
# to 1153s (Nov 2026 CI); the slice keeps a 25% margin over that.
IMAGE_PHASE_MIN_SECONDS: Final[int] = 1440
RUNTIME_PHASE_CAP_SECONDS: Final[int] = 600
# Reserved up front for OrbStack recovery, so a slow initial readiness gate cannot starve it.
RUNTIME_RECOVERY_RESERVE_SECONDS: Final[int] = 360
RUNTIME_INITIAL_MIN_SECONDS: Final[int] = 60
RUNTIME_PHASE_MIN_SECONDS: Final[int] = (
    RUNTIME_INITIAL_MIN_SECONDS + RUNTIME_RECOVERY_RESERVE_SECONDS
)
# A runtime lost mid-scenario gets the same OrbStack recovery slice as the runtime phase.
SCENARIO_RECOVERY_CAP_SECONDS: Final[int] = RUNTIME_RECOVERY_RESERVE_SECONDS
# Left after scenario recovery for the retried step and the phases after it. Hosted macOS
# exercise-sidecars took up to 411s plus 41s of later phases (Nov 2026 CI); ~30% margin.
SCENARIO_RETRY_RESERVE_SECONDS: Final[int] = 600
# Never start a blocking command with less time than this left before its deadline.
MIN_COMMAND_SECONDS: Final[float] = 1.0
BUDGET_EXHAUSTED: Final[str] = "budget_exhausted"
PHASE_CAP: Final[str] = "phase-cap"
JOB_RESERVE: Final[str] = "job-reserve"


class BudgetExhaustedError(FreshHostError):
    """A phase or slice has no time left; the message starts with `budget_exhausted`."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{BUDGET_EXHAUSTED}: {detail}")


class Clock(Protocol):
    """Wall and monotonic time source; injectable so budget logic is testable."""

    def time(self) -> float:
        """Return wall-clock seconds since the epoch."""
        ...

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for *seconds*."""
        ...


class SystemClock:
    """Clock backed by the `time` module."""

    def time(self) -> float:
        """Return wall-clock seconds since the epoch."""
        return time.time()

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Block for *seconds*."""
        time.sleep(seconds)


SYSTEM_CLOCK: Final[Clock] = SystemClock()


@dataclass(frozen=True, slots=True)
class JobBudget:
    """Absolute job deadline plus the time reserved for post-pull phases."""

    started_epoch: float
    deadline_epoch: float
    scenario_reserve_seconds: int
    observer_reserve_seconds: int

    def to_env(self) -> dict[str, str]:
        """Serialize the budget into `GITHUB_ENV` assignments."""
        return {
            JOB_STARTED_ENV: f"{self.started_epoch:.3f}",
            JOB_DEADLINE_ENV: f"{self.deadline_epoch:.3f}",
            SCENARIO_RESERVE_ENV: str(self.scenario_reserve_seconds),
            OBSERVER_RESERVE_ENV: str(self.observer_reserve_seconds),
        }

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> JobBudget | None:
        """Load a recorded budget; `None` when no budget was recorded (local runs)."""
        present = [key for key in _BUDGET_ENV_KEYS if environ.get(key, "").strip()]
        if not present:
            return None
        if len(present) != len(_BUDGET_ENV_KEYS):
            missing = sorted(set(_BUDGET_ENV_KEYS) - set(present))
            raise FreshHostError(f"incomplete fresh-host job budget; missing {missing}")
        budget = cls(
            started_epoch=_parse_float(environ, JOB_STARTED_ENV),
            deadline_epoch=_parse_float(environ, JOB_DEADLINE_ENV),
            scenario_reserve_seconds=_parse_seconds(environ, SCENARIO_RESERVE_ENV),
            observer_reserve_seconds=_parse_seconds(environ, OBSERVER_RESERVE_ENV),
        )
        if budget.deadline_epoch <= budget.started_epoch:
            raise FreshHostError("fresh-host job budget deadline precedes its start")
        return budget


def _parse_float(environ: Mapping[str, str], key: str) -> float:
    """Parse one numeric budget variable."""
    try:
        return float(environ[key])
    except ValueError as exc:
        raise FreshHostError(f"{key} must be numeric, got {environ[key]!r}") from exc


def _parse_seconds(environ: Mapping[str, str], key: str) -> int:
    """Parse one non-negative whole-second budget variable."""
    try:
        value = int(environ[key])
    except ValueError as exc:
        raise FreshHostError(f"{key} must be an integer, got {environ[key]!r}") from exc
    if value < 0:
        raise FreshHostError(f"{key} must not be negative")
    return value


def initialize_job_budget(
    *,
    job_timeout_minutes: int,
    scenario_reserve_minutes: int,
    observer_reserve_minutes: int,
    environ: Mapping[str, str],
    clock: Clock = SYSTEM_CLOCK,
) -> JobBudget:
    """Record the job budget once; an already recorded budget is returned unchanged."""
    existing = JobBudget.from_env(environ)
    if existing is not None:
        return existing
    if min(job_timeout_minutes, scenario_reserve_minutes, observer_reserve_minutes) <= 0:
        raise FreshHostError("job timeout and phase reserves must be positive")
    job_seconds = job_timeout_minutes * 60 - JOB_START_SKEW_SECONDS
    reserved_seconds = (scenario_reserve_minutes + observer_reserve_minutes) * 60
    if reserved_seconds >= job_seconds:
        raise FreshHostError(
            f"phase reserves ({reserved_seconds}s) leave no pull budget inside the "
            f"{job_timeout_minutes}-minute job cap"
        )
    started = clock.time()
    return JobBudget(
        started_epoch=started,
        deadline_epoch=started + job_seconds,
        scenario_reserve_seconds=scenario_reserve_minutes * 60,
        observer_reserve_seconds=observer_reserve_minutes * 60,
    )


def record_job_budget(
    *,
    job_timeout_minutes: int,
    scenario_reserve_minutes: int,
    observer_reserve_minutes: int,
    environ: Mapping[str, str],
    github_env_file: Path | None,
    clock: Clock = SYSTEM_CLOCK,
) -> JobBudget:
    """Initialize the job budget and export it to later steps."""
    budget = initialize_job_budget(
        job_timeout_minutes=job_timeout_minutes,
        scenario_reserve_minutes=scenario_reserve_minutes,
        observer_reserve_minutes=observer_reserve_minutes,
        environ=environ,
        clock=clock,
    )
    write_github_env(budget.to_env(), github_env_file)
    return budget


@dataclass(frozen=True, slots=True)
class PhaseDeadline:
    """One phase deadline, pinned to the monotonic clock of the current process."""

    wall_deadline_epoch: float
    monotonic_deadline: float
    limited_by: str

    @classmethod
    def after(
        cls, seconds: float, *, clock: Clock = SYSTEM_CLOCK, limited_by: str = PHASE_CAP
    ) -> PhaseDeadline:
        """Return a deadline *seconds* from now."""
        return cls(
            wall_deadline_epoch=clock.time() + seconds,
            monotonic_deadline=clock.monotonic() + seconds,
            limited_by=limited_by,
        )

    def remaining(self, clock: Clock) -> float:
        """Return the seconds left before the deadline, never negative."""
        return max(0.0, self.monotonic_deadline - clock.monotonic())

    def clip(self, seconds: float, clock: Clock) -> float:
        """Return *seconds* shortened to the time left before the deadline."""
        return min(seconds, self.remaining(clock))

    def command_timeout(self, cap_seconds: float, clock: Clock) -> float:
        """Return the timeout for one blocking command; raise when too little time is left."""
        timeout = self.clip(cap_seconds, clock)
        if timeout < MIN_COMMAND_SECONDS:
            raise BudgetExhaustedError(f"{timeout:.1f}s left before the {self.limited_by} deadline")
        return timeout

    def slice(
        self,
        clock: Clock,
        *,
        cap_seconds: float | None = None,
        reserve_seconds: float = 0.0,
        limited_by: str | None = None,
    ) -> PhaseDeadline:
        """Return min(now + cap, this deadline - reserve); never later than this deadline.

        *limited_by* labels the slice when its cap, not this deadline, is the bound.
        """
        end = self.monotonic_deadline - max(0.0, reserve_seconds)
        label = self.limited_by
        if cap_seconds is not None:
            capped_end = clock.monotonic() + cap_seconds
            if capped_end < end:
                end = capped_end
                label = limited_by or label
        return PhaseDeadline(
            wall_deadline_epoch=self.wall_deadline_epoch - (self.monotonic_deadline - end),
            monotonic_deadline=end,
            limited_by=label,
        )


def phase_deadline(
    budget: JobBudget | None,
    *,
    phase: str,
    phase_cap_seconds: int,
    later_reserve_seconds: int = 0,
    clock: Clock = SYSTEM_CLOCK,
) -> PhaseDeadline:
    """Return min(now + phase cap, job deadline - reserves of every later phase).

    The scenario and observer reserves are always later phases; *later_reserve_seconds*
    adds the minimum slices of any phase that runs between this one and the scenario.
    """
    reserve_limit_epoch = (
        None
        if budget is None
        else budget.deadline_epoch
        - budget.scenario_reserve_seconds
        - budget.observer_reserve_seconds
        - later_reserve_seconds
    )
    return _bounded_deadline(
        phase=phase,
        phase_cap_seconds=phase_cap_seconds,
        reserve_limit_epoch=reserve_limit_epoch,
        clock=clock,
    )


def _bounded_deadline(
    *,
    phase: str,
    phase_cap_seconds: int,
    reserve_limit_epoch: float | None,
    clock: Clock,
) -> PhaseDeadline:
    """Return min(now + phase cap, reserve limit); raise when that is already past."""
    now_wall = clock.time()
    now_monotonic = clock.monotonic()
    deadline = now_wall + phase_cap_seconds
    limited_by = PHASE_CAP
    if reserve_limit_epoch is not None and reserve_limit_epoch < deadline:
        deadline = reserve_limit_epoch
        limited_by = JOB_RESERVE
    if deadline <= now_wall:
        raise BudgetExhaustedError(
            f"no {phase} time remains before the later-phase reserves "
            f"(overdue by {now_wall - deadline:.0f}s)"
        )
    return PhaseDeadline(
        wall_deadline_epoch=deadline,
        monotonic_deadline=now_monotonic + (deadline - now_wall),
        limited_by=limited_by,
    )


def runtime_phase_deadline(
    budget: JobBudget | None, *, clock: Clock = SYSTEM_CLOCK
) -> PhaseDeadline:
    """Return the runtime readiness deadline; it leaves the image phase its minimum slice."""
    return phase_deadline(
        budget,
        phase="runtime",
        phase_cap_seconds=RUNTIME_PHASE_CAP_SECONDS,
        later_reserve_seconds=IMAGE_PHASE_MIN_SECONDS,
        clock=clock,
    )


def image_phase_deadline(budget: JobBudget | None, *, clock: Clock = SYSTEM_CLOCK) -> PhaseDeadline:
    """Return the image phase deadline; it gets whatever runtime left, up to its cap."""
    return phase_deadline(
        budget,
        phase="image",
        phase_cap_seconds=IMAGE_PHASE_CAP_SECONDS,
        clock=clock,
    )


def scenario_phase_deadline(budget: JobBudget, *, clock: Clock = SYSTEM_CLOCK) -> PhaseDeadline:
    """Return min(now + scenario reserve, job deadline - observer reserve).

    Call it when the scenario starts: the scenario step's timeout equals its reserve, and the
    observers that upload evidence keep their reserve however late the scenario began.
    """
    return _bounded_deadline(
        phase="scenario",
        phase_cap_seconds=budget.scenario_reserve_seconds,
        reserve_limit_epoch=budget.deadline_epoch - budget.observer_reserve_seconds,
        clock=clock,
    )
