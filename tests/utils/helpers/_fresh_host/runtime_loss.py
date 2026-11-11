"""Runtime-loss recovery for hosted macOS fresh-host scenario steps.

The runtime and image phases recover OrbStack before the scenario starts. A VM that dies
once the scenario runs would otherwise fail the job, so Docker-backed steps run through
`run_with_runtime_recovery`: when a step fails and a live probe finds the backend gone, the
runtime phase's OrbStack recovery runs inside a slice of the scenario's recovery window and
the step is retried once. Without a recorded job budget (local runs) or off hosted macOS OrbStack,
failures pass through untouched.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TypeVar

from tests.utils.helpers._fresh_host.budget import (
    SCENARIO_RECOVERY_CAP_SECONDS,
    SCENARIO_RETRY_RESERVE_SECONDS,
    SYSTEM_CLOCK,
    BudgetExhaustedError,
    Clock,
    JobBudget,
    PhaseDeadline,
    scenario_phase_deadline,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import log
from tests.utils.helpers._hosted_docker.io import write_json
from tests.utils.helpers._hosted_docker.runtime import recover_lost_runtime

RUNTIME_LOSS: Final[str] = "runtime_loss"
RUNTIME_LOSS_CAP: Final[str] = "runtime-loss-cap"

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class RecoveryWindow:
    """The scenario deadline runtime-loss recovery runs inside, with the clock it is pinned to."""

    deadline: PhaseDeadline
    clock: Clock


def arm_runtime_recovery(
    context: FreshHostContext,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> RecoveryWindow | None:
    """Return the window runtime-loss recovery runs inside; `None` leaves recovery off.

    Recovery is enabled only on hosted macOS OrbStack runs that recorded a job budget. Call
    it when the scenario starts, so the window follows the scenario step's own timeout.
    """
    if context.platform != "macos" or (context.runtime_provider or "orbstack") != "orbstack":
        return None
    budget = JobBudget.from_env(os.environ if environ is None else environ)
    if budget is None:
        return None
    try:
        deadline = scenario_phase_deadline(budget, clock=clock)
    except BudgetExhaustedError as exc:
        log(f"Runtime-loss recovery disabled: {exc}.")
        return None
    log(
        f"Runtime-loss recovery armed: {deadline.remaining(clock):.0f}s scenario window, "
        f"limited by {deadline.limited_by}."
    )
    return RecoveryWindow(deadline=deadline, clock=clock)


def _recovery_slice(deadline: PhaseDeadline, clock: Clock) -> PhaseDeadline:
    """Return the recovery slice; it always leaves the retried step its reserve."""
    return deadline.slice(
        clock,
        cap_seconds=SCENARIO_RECOVERY_CAP_SECONDS,
        reserve_seconds=SCENARIO_RETRY_RESERVE_SECONDS,
        limited_by=RUNTIME_LOSS_CAP,
    )


def _recover(
    context: FreshHostContext,
    *,
    step: str,
    failure: FreshHostError,
    window: RecoveryWindow,
) -> bool:
    """Recover a lost runtime; `False` means the failure was not runtime loss."""
    clock = window.clock
    try:
        report = recover_lost_runtime(
            context, step=step, deadline=_recovery_slice(window.deadline, clock), clock=clock
        )
    except BudgetExhaustedError as exc:
        raise BudgetExhaustedError(
            f"{RUNTIME_LOSS} recovery for {step} has no time left ({exc}); step failure: "
            f"{failure}"
        ) from failure
    if report is None:
        return False
    write_json(
        asdict(report),
        Path(context.diagnostics_dir).resolve() / "runtime-loss" / step / "report.json",
    )
    if report.recovered:
        return True
    detail = (
        f"{RUNTIME_LOSS} during {step} ({report.trigger_reason}); "
        f"last reason: {report.last_failure_reason}; step failure: {failure}"
    )
    if report.exhausted:
        raise BudgetExhaustedError(
            f"{RUNTIME_LOSS} recovery ran out of time: {detail}"
        ) from failure
    if not report.recovery_attempts:
        raise FreshHostError(f"unrecoverable {detail}") from failure
    raise FreshHostError(f"orbstack_recovery_failed: {detail}") from failure


def run_with_runtime_recovery(
    context: FreshHostContext,
    *,
    step: str,
    window: RecoveryWindow | None,
    action: Callable[[], _T],
    retry_action: Callable[[], _T] | None = None,
) -> _T:
    """Run one Docker-backed step; after a recovered runtime loss, retry it once.

    *retry_action* replaces *action* for the retry when the step must rebuild state the
    lost runtime took with it. A `None` *window* leaves recovery off.
    """
    try:
        return action()
    except FreshHostError as failure:
        if window is None:
            raise
        log(f"{step} failed ({failure}); probing the Docker runtime for loss.")
        if not _recover(context, step=step, failure=failure, window=window):
            log(f"Docker runtime is reachable; {step} failed for another reason.")
            raise
    log(f"Docker runtime recovered; retrying {step} once.")
    return (retry_action or action)()
