"""Compose image discovery and pull orchestration for hosted Docker CI."""

from __future__ import annotations

import concurrent.futures
import os
import random
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import load_env_assignments, varlock_local_env_file
from tests.utils.helpers._fresh_host.budget import (
    BUDGET_EXHAUSTED,
    MIN_COMMAND_SECONDS,
    SYSTEM_CLOCK,
    Clock,
    JobBudget,
    PhaseDeadline,
    image_phase_deadline,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.disk import (
    INSUFFICIENT_DISK,
    docker_root_dir,
    ensure_pull_capacity,
    remove_with_sudo,
)
from tests.utils.helpers._hosted_docker.events import PullEventLog
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_json
from tests.utils.helpers._hosted_docker.models import (
    DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    MAX_STALL_RETRIES,
    MIN_PULL_ATTEMPT_SECONDS,
    PULL_HEARTBEAT_SECONDS,
    PULL_RECOVERY_CAP_SECONDS,
    PULL_RETRY_BACKOFF_CAP_SECONDS,
    REGISTRY_RETRY_BACKOFF_BASE_SECONDS,
    REGISTRY_RETRY_BACKOFF_CAP_SECONDS,
    ImageEnsureReport,
    PullAttemptRecord,
    PullReport,
    RuntimeInstallReport,
)
from tests.utils.helpers._hosted_docker.pull_client import (
    RETRYABLE_FAILURE_CLASSES,
    STALL_TIMEOUT_SECONDS,
    CliDockerPullClient,
    DockerPullClient,
    PullOutcome,
)
from tests.utils.helpers._hosted_docker.runtime import wait_runtime_ready
from tests.utils.helpers._hosted_docker.shell import (
    run_checked,
    run_command,
    wait_for_docker_ready,
)

COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER: Final[str] = "compose-image-resolution-placeholder"
PULL_EVENTS_FILE_NAME: Final[str] = "image-pull-events.jsonl"
DISK_PREFLIGHT_FILE_NAME: Final[str] = "disk-preflight.json"
COMPOSE_RESOLUTION_TIMEOUT_SECONDS: Final[int] = 120
IMAGE_INSPECT_TIMEOUT_SECONDS: Final[int] = 60
DOCKER_ROOT_PROBE_TIMEOUT_SECONDS: Final[int] = 60
RUNNER_PATH_REMOVAL_TIMEOUT_SECONDS: Final[int] = 600
_RECOVERY_CAP_LABEL: Final[str] = "recovery-cap"

type RuntimeRecovery = Callable[[PhaseDeadline], object]


def _command_timeout(cap_seconds: float, deadline: PhaseDeadline | None, clock: Clock) -> float:
    """Clip one blocking command to the phase deadline, when there is one."""
    return cap_seconds if deadline is None else deadline.command_timeout(cap_seconds, clock)


def compose_probe_env(
    *,
    context: FreshHostContext,
    repo_root: Path,
    compose_state_dir_name: str,
) -> dict[str, str]:
    """Build an environment suitable for compose image resolution calls."""
    compose_state_dir = Path(context.tmp_root).resolve() / compose_state_dir_name
    compose_state_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for key, value in load_env_assignments(varlock_local_env_file(repo_root)).items():
        if value and not env.get(key, "").strip():
            env[key] = value
    # `docker compose config --images` still resolves env interpolation before setup creates
    # the repo-local Varlock contract, so pre-fill required secrets with placeholders.
    for key in ("LITELLM_DB_PASSWORD", "NEO4J_PASSWORD"):
        if not env.get(key, "").strip():
            env[key] = COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER
    env["STRONGCLAW_COMPOSE_STATE_DIR"] = str(compose_state_dir)
    if context.compose_variant is not None:
        env["STRONGCLAW_COMPOSE_VARIANT"] = context.compose_variant
    return env


def resolve_compose_images(
    compose_files: Sequence[Path],
    *,
    cwd: Path,
    env: dict[str, str],
    deadline: PhaseDeadline | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> list[str]:
    """Resolve compose image references in first-seen order."""
    images: list[str] = []
    seen: set[str] = set()
    for compose_file in compose_files:
        completed = run_checked(
            ["docker", "compose", "-f", str(compose_file), "config", "--images"],
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout_seconds=_command_timeout(COMPOSE_RESOLUTION_TIMEOUT_SECONDS, deadline, clock),
        )
        for raw_line in completed.stdout.splitlines():
            image = raw_line.strip()
            if image and image not in seen:
                seen.add(image)
                images.append(image)
    if not images:
        raise FreshHostError("No compose images were resolved.")
    return images


def list_local_images(
    images: Sequence[str],
    *,
    deadline: PhaseDeadline | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> list[str]:
    """Return the image refs already present in the local daemon.

    An inspect that outlives its clipped timeout counts as absent, so it is pulled or
    reported missing instead of hanging the phase.
    """
    present: list[str] = []
    for image in images:
        try:
            result = run_command(
                ["docker", "image", "inspect", image],
                cwd=Path.cwd(),
                env=dict(os.environ),
                timeout_seconds=_command_timeout(IMAGE_INSPECT_TIMEOUT_SECONDS, deadline, clock),
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            log(f"docker image inspect {image} timed out; treating it as absent.")
            continue
        if result.returncode == 0:
            present.append(image)
    return present


def _retry_backoff_seconds(*, attempt_count: int, registry_failure: bool) -> int:
    """Return the backoff before the next pull attempt.

    Transient registry connectivity failures use a longer exponential backoff so a brief
    upstream outage is ridden out instead of exhausting the retry budget in seconds.
    """
    if registry_failure:
        growth = REGISTRY_RETRY_BACKOFF_BASE_SECONDS << max(0, attempt_count - 1)
        return min(REGISTRY_RETRY_BACKOFF_CAP_SECONDS, growth)
    return min(PULL_RETRY_BACKOFF_CAP_SECONDS, 2 * attempt_count)


def _attempt_timeout_seconds(
    pull_timeout_seconds: int, deadline: PhaseDeadline | None, clock: Clock
) -> int:
    """Clip one pull attempt's timeout so it cannot outlive the phase deadline."""
    if deadline is None:
        return pull_timeout_seconds
    return max(1, min(pull_timeout_seconds, int(deadline.remaining(clock))))


def _recovery_deadline(deadline: PhaseDeadline | None, clock: Clock) -> PhaseDeadline:
    """Return the runtime recovery slice; it leaves room for one more pull attempt."""
    if deadline is None:
        return PhaseDeadline.after(
            PULL_RECOVERY_CAP_SECONDS, clock=clock, limited_by=_RECOVERY_CAP_LABEL
        )
    return deadline.slice(
        clock,
        cap_seconds=PULL_RECOVERY_CAP_SECONDS,
        reserve_seconds=MIN_PULL_ATTEMPT_SECONDS,
        limited_by=_RECOVERY_CAP_LABEL,
    )


def _orbstack_recovery(context_path: Path, clock: Clock) -> RuntimeRecovery:
    """Return a runtime restart that stays inside the slice the pull loop hands it."""

    def recover(recovery_deadline: PhaseDeadline) -> RuntimeInstallReport:
        return wait_runtime_ready(context_path, deadline=recovery_deadline, clock=clock)

    return recover


def _budget_allows(deadline: PhaseDeadline | None, clock: Clock, *, spend: float = 0.0) -> bool:
    """Return whether *spend* seconds still leave room for one more pull attempt."""
    if deadline is None:
        return True
    return deadline.remaining(clock) - spend >= MIN_PULL_ATTEMPT_SECONDS


def _full_jitter(cap_seconds: float) -> float:
    """Return a full-jitter delay in [0, cap_seconds]."""
    return random.uniform(0.0, cap_seconds)


def _overall_failure_class(
    *,
    budget_exhausted: bool,
    terminal: Sequence[PullOutcome],
    last_classes: Sequence[str],
) -> str | None:
    """Pick the report-level failure class, preferring non-retryable causes."""
    if terminal:
        return terminal[0].failure_class or "unknown"
    if budget_exhausted:
        return BUDGET_EXHAUSTED
    return last_classes[-1] if last_classes else None


def pull_images(
    images: Sequence[str],
    *,
    parallelism: int,
    max_attempts: int,
    pull_timeout_seconds: int = DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    recovery_cwd: Path | None = None,
    recovery_env: dict[str, str] | None = None,
    deadline: PhaseDeadline | None = None,
    clock: Clock = SYSTEM_CLOCK,
    event_log: PullEventLog | None = None,
    recover_runtime: RuntimeRecovery | None = None,
    client: DockerPullClient | None = None,
    jitter: Callable[[float], float] = _full_jitter,
    stall_seconds: float = STALL_TIMEOUT_SECONDS,
) -> PullReport:
    """Pull images with classified, budget-clipped retries and stall-aware pulls."""
    if parallelism < 1 or max_attempts < 1 or pull_timeout_seconds < 1:
        raise FreshHostError("parallelism, max_attempts, and pull_timeout_seconds must be positive")
    if (recovery_cwd is None) != (recovery_env is None):
        raise FreshHostError("recovery_cwd and recovery_env must be provided together")
    pull_client = client if client is not None else CliDockerPullClient(clock=clock)

    def emit(event: str, **fields: object) -> None:
        if event_log is not None:
            event_log.emit(event, **fields)

    def pull_within_budget(image: str) -> PullOutcome:
        # Timeouts are computed when the pull actually starts, so queued pulls see the
        # budget that is left then and never start once it is gone.
        if not _budget_allows(deadline, clock):
            return PullOutcome(
                image=image,
                returncode=1,
                duration_seconds=0.0,
                output_tail="pull not started: image-pull budget exhausted",
                failure_class="budget_exhausted",
            )
        timeout = _attempt_timeout_seconds(pull_timeout_seconds, deadline, clock)
        return pull_client.pull(image, timeout_seconds=timeout, stall_seconds=stall_seconds)

    outstanding = list(images)
    pulled_images: list[str] = []
    retried_images: list[str] = []
    seen_retries: set[str] = set()
    terminal_failures: list[PullOutcome] = []
    stall_retries: dict[str, int] = {}
    attempt_records: list[PullAttemptRecord] = []
    last_classes: list[str] = []
    attempt_parallelism = parallelism
    attempt_count = 0
    budget_exhausted = False
    while outstanding and attempt_count < max_attempts:
        if not _budget_allows(deadline, clock):
            budget_exhausted = True
            break
        attempt_count += 1
        log(
            f"Pulling {len(outstanding)} image(s) with parallelism={attempt_parallelism} "
            f"(attempt {attempt_count}/{max_attempts})."
        )
        emit(
            "attempt_started",
            attempt=attempt_count,
            images=list(outstanding),
            parallelism=attempt_parallelism,
            remaining_seconds=None if deadline is None else round(deadline.remaining(clock), 1),
        )
        retryable: list[str] = []
        attempt_classes: set[str] = set()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(attempt_parallelism, len(outstanding))
        ) as executor:
            futures = {executor.submit(pull_within_budget, image): image for image in outstanding}
            pending = set(futures)
            while pending:
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=PULL_HEARTBEAT_SECONDS,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    log("Waiting on " + ", ".join(sorted(futures[future] for future in pending)))
                    continue
                for future in done:
                    outcome = future.result()
                    record = PullAttemptRecord(
                        image=outcome.image,
                        attempt=attempt_count,
                        returncode=outcome.returncode,
                        duration_seconds=round(outcome.duration_seconds, 3),
                        failure_class=outcome.failure_class,
                        seconds_since_progress=round(outcome.seconds_since_progress, 1),
                        bytes_observed=outcome.bytes_observed,
                        output_tail=outcome.output_tail[-2000:],
                    )
                    attempt_records.append(record)
                    emit("image_result", **asdict(record))
                    if outcome.ok:
                        log(f"[ok] {outcome.image} in {outcome.duration_seconds:.1f}s")
                        pulled_images.append(outcome.image)
                        continue
                    failure_class = outcome.failure_class or "unknown"
                    log(
                        f"[failed:{failure_class}] {outcome.image} "
                        f"in {outcome.duration_seconds:.1f}s"
                    )
                    if outcome.output_tail:
                        print(outcome.output_tail, flush=True)
                    attempt_classes.add(failure_class)
                    last_classes.append(failure_class)
                    if failure_class == "stalled":
                        stall_retries[outcome.image] = stall_retries.get(outcome.image, 0) + 1
                    if failure_class == "budget_exhausted":
                        budget_exhausted = True
                        retryable.append(outcome.image)
                    elif failure_class not in RETRYABLE_FAILURE_CLASSES or (
                        stall_retries.get(outcome.image, 0) > MAX_STALL_RETRIES
                    ):
                        terminal_failures.append(outcome)
                    else:
                        retryable.append(outcome.image)
        outstanding = retryable
        if not outstanding or budget_exhausted or attempt_count >= max_attempts:
            break
        for image in outstanding:
            if image not in seen_retries:
                seen_retries.add(image)
                retried_images.append(image)
        if not _budget_allows(deadline, clock):
            budget_exhausted = True
            break
        daemon_failure = "daemon_unavailable" in attempt_classes
        registry_failure = "registry_transient" in attempt_classes and not daemon_failure
        if recovery_cwd is not None and recovery_env is not None:
            if daemon_failure:
                log("Detected Docker daemon connectivity failure; waiting for runtime recovery.")
            elif registry_failure:
                log("Detected transient registry connectivity failure; backing off before retry.")
            else:
                log("Retrying image pull after failure; probing Docker runtime before retry.")
            recovery_deadline = _recovery_deadline(deadline, clock)
            if recovery_deadline.remaining(clock) < MIN_COMMAND_SECONDS:
                budget_exhausted = True
                break
            try:
                if daemon_failure and recover_runtime is not None:
                    # A dead runtime needs a restart, not just a wait (OrbStack on macOS).
                    emit(
                        "runtime_recovery",
                        attempt=attempt_count,
                        remaining_seconds=round(recovery_deadline.remaining(clock), 1),
                    )
                    recover_runtime(recovery_deadline)
                else:
                    wait_for_docker_ready(
                        cwd=recovery_cwd,
                        env=recovery_env,
                        deadline=recovery_deadline,
                        clock=clock,
                    )
            except FreshHostError as exc:
                log(f"Docker daemon recovery probe failed: {exc}")
                emit("recovery_failed", attempt=attempt_count, error=str(exc))
                return PullReport(
                    exit_code=1,
                    pulled_images=pulled_images,
                    failed_images=[outcome.image for outcome in terminal_failures] + outstanding,
                    attempt_count=attempt_count,
                    retried_images=retried_images,
                    failure_class="daemon_unavailable",
                    attempts=attempt_records,
                )
        next_parallelism = max(1, attempt_parallelism // 2)
        if next_parallelism != attempt_parallelism:
            log(f"Reducing retry parallelism {attempt_parallelism}->{next_parallelism}.")
        attempt_parallelism = next_parallelism
        backoff_seconds = round(
            jitter(
                _retry_backoff_seconds(
                    attempt_count=attempt_count, registry_failure=registry_failure
                )
            ),
            3,
        )
        if not _budget_allows(deadline, clock, spend=backoff_seconds):
            budget_exhausted = True
            break
        log(f"Retrying {len(outstanding)} image(s) after {backoff_seconds}s.")
        emit("backoff", attempt=attempt_count, seconds=backoff_seconds)
        clock.sleep(backoff_seconds)
    if budget_exhausted and outstanding:
        remaining = None if deadline is None else round(deadline.remaining(clock), 1)
        log(f"Image pull budget exhausted with {len(outstanding)} image(s) outstanding.")
        emit("budget_exhausted", attempt=attempt_count, remaining_seconds=remaining)
    failed_images = [outcome.image for outcome in terminal_failures] + outstanding
    return PullReport(
        exit_code=0 if not failed_images else 1,
        pulled_images=pulled_images,
        failed_images=failed_images,
        attempt_count=attempt_count,
        retried_images=retried_images,
        failure_class=(
            _overall_failure_class(
                budget_exhausted=budget_exhausted and bool(outstanding),
                terminal=terminal_failures,
                last_classes=last_classes,
            )
            if failed_images
            else None
        ),
        attempts=attempt_records,
    )


def _failure_class_for(reason: str) -> str:
    """Classify a pre-pull failure message for the ensure report."""
    for failure_class in (BUDGET_EXHAUSTED, INSUFFICIENT_DISK):
        if reason.startswith(failure_class):
            return failure_class
    return "ensure_failed"


def preflight_linux_disk(
    *,
    context: FreshHostContext,
    repo_root: Path,
    env: Mapping[str, str],
    missing_images: Sequence[str],
    event_log: PullEventLog,
    deadline: PhaseDeadline,
    clock: Clock = SYSTEM_CLOCK,
) -> str | None:
    """Assert Linux pull capacity before pulling; return the preflight report path."""
    if context.platform != "linux" or not missing_images:
        return None
    report_path = Path(context.report_dir).resolve() / DISK_PREFLIGHT_FILE_NAME

    def remove_within_deadline(path: Path) -> None:
        remove_with_sudo(
            path,
            timeout_seconds=deadline.command_timeout(RUNNER_PATH_REMOVAL_TIMEOUT_SECONDS, clock),
        )

    try:
        report = ensure_pull_capacity(
            docker_root=docker_root_dir(
                cwd=repo_root,
                env=env,
                timeout_seconds=deadline.command_timeout(DOCKER_ROOT_PROBE_TIMEOUT_SECONDS, clock),
            ),
            missing_images=missing_images,
            report_path=report_path,
            remover=remove_within_deadline,
        )
    except FreshHostError as exc:
        event_log.emit("disk_preflight", sufficient=False, error=str(exc))
        raise
    event_log.emit(
        "disk_preflight",
        sufficient=True,
        required_bytes=report.required_bytes,
        free_before_bytes=report.free_before_bytes,
        free_after_bytes=report.free_after_bytes,
        removed_paths=report.removed_paths,
    )
    return str(report_path)


def ensure_images(
    context_path: Path,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> ImageEnsureReport:
    """Ensure the scenario's compose images exist locally within the job budget."""
    context = load_context(context_path)
    report_path = Path(context.image_report_path).resolve() if context.image_report_path else None
    repo_root = Path(context.repo_root).resolve()
    compose_files = [Path(path).resolve() for path in context.compose_files]
    started_at = now_iso()
    started = clock.monotonic()
    events_path = Path(context.report_dir).resolve() / PULL_EVENTS_FILE_NAME
    state: dict[str, list[str]] = {
        "images": [],
        "local_before": [],
        "missing_before_pull": [],
        "missing_after_pull": [],
    }
    pull_report: PullReport | None = None
    deadline: PhaseDeadline | None = None
    artifacts: dict[str, str | None] = {"disk_preflight_path": None}

    def build_report(failure_reason: str | None, failure_class: str | None) -> ImageEnsureReport:
        finished_at = now_iso()
        return ImageEnsureReport(
            compose_files=[str(path) for path in compose_files],
            images=state["images"],
            local_before=state["local_before"],
            missing_before_pull=state["missing_before_pull"],
            pulled_images=[] if pull_report is None else list(pull_report.pulled_images),
            missing_after_pull=state["missing_after_pull"],
            pull_parallelism=context.docker_pull_parallelism,
            pull_attempt_count=0 if pull_report is None else pull_report.attempt_count,
            retried_images=[] if pull_report is None else list(pull_report.retried_images),
            failure_reason=failure_reason,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(clock.monotonic() - started, 3),
            created_at=finished_at,
            failure_class=failure_class,
            pull_deadline_epoch=None if deadline is None else deadline.wall_deadline_epoch,
            pull_deadline_limited_by=None if deadline is None else deadline.limited_by,
            pull_events_path=str(events_path) if context.ensure_images else None,
            disk_preflight_path=artifacts["disk_preflight_path"],
        )

    def persist(report: ImageEnsureReport) -> ImageEnsureReport:
        if report_path is not None:
            write_json(asdict(report), report_path)
        return report

    if not context.ensure_images:
        return persist(build_report(None, None))

    try:
        deadline = image_phase_deadline(
            JobBudget.from_env(os.environ if environ is None else environ), clock=clock
        )
        event_log = PullEventLog(events_path)
        event_log.emit(
            "phase_started",
            deadline_epoch=deadline.wall_deadline_epoch,
            limited_by=deadline.limited_by,
            remaining_seconds=round(deadline.remaining(clock), 1),
        )
        env = compose_probe_env(
            context=context, repo_root=repo_root, compose_state_dir_name="compose-prepull"
        )
        state["images"] = resolve_compose_images(
            compose_files, cwd=repo_root, env=env, deadline=deadline, clock=clock
        )
        state["local_before"] = list_local_images(state["images"], deadline=deadline, clock=clock)
        state["missing_before_pull"] = [
            image for image in state["images"] if image not in state["local_before"]
        ]
        artifacts["disk_preflight_path"] = preflight_linux_disk(
            context=context,
            repo_root=repo_root,
            env=env,
            missing_images=state["missing_before_pull"],
            event_log=event_log,
            deadline=deadline,
            clock=clock,
        )
        if state["missing_before_pull"]:
            pull_report = pull_images(
                state["missing_before_pull"],
                parallelism=context.docker_pull_parallelism,
                max_attempts=context.docker_pull_max_attempts,
                recovery_cwd=repo_root,
                recovery_env=env,
                deadline=deadline,
                clock=clock,
                event_log=event_log,
                recover_runtime=(
                    _orbstack_recovery(context_path, clock) if context.platform == "macos" else None
                ),
            )
        local_after_pull = list_local_images(state["images"], deadline=deadline, clock=clock)
        state["missing_after_pull"] = [
            image for image in state["images"] if image not in local_after_pull
        ]
    except Exception as exc:
        # Evidence first: a failed ensure must still leave a report for the observers.
        persist(build_report(str(exc), _failure_class_for(str(exc))))
        raise

    failure_reason: str | None = None
    failure_class: str | None = None
    if pull_report is not None and pull_report.exit_code != 0:
        failure_reason = "docker pull failed"
        failure_class = pull_report.failure_class or "pull_failed"
    elif state["missing_after_pull"]:
        failure_reason = "images remain unavailable after pull"
        failure_class = "images_missing"
    report = persist(build_report(failure_reason, failure_class))
    event_log.emit("phase_finished", failure_class=failure_class)
    if failure_reason is not None:
        raise FreshHostError(f"{failure_reason} ({failure_class})")
    return report
