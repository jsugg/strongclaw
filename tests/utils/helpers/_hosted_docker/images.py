"""Compose image discovery and pull orchestration for hosted Docker CI."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import load_env_assignments, varlock_local_env_file
from tests.utils.helpers._fresh_host.budget import (
    SYSTEM_CLOCK,
    Clock,
    JobBudget,
    PhaseDeadline,
    pull_phase_deadline,
)
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.events import PullEventLog
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_json
from tests.utils.helpers._hosted_docker.models import (
    BUDGET_EXHAUSTED,
    DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    MAX_RECOVERY_PROBES,
    MIN_PULL_ATTEMPT_SECONDS,
    PULL_HEARTBEAT_SECONDS,
    PULL_RETRY_BACKOFF_CAP_SECONDS,
    READINESS_PROBE_SECONDS,
    REGISTRY_RETRY_BACKOFF_BASE_SECONDS,
    REGISTRY_RETRY_BACKOFF_CAP_SECONDS,
    ImageEnsureReport,
    PullReport,
)
from tests.utils.helpers._hosted_docker.runtime import wait_runtime_ready
from tests.utils.helpers._hosted_docker.shell import run_checked, wait_for_docker_ready

COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER: Final[str] = "compose-image-resolution-placeholder"
PULL_EVENTS_FILE_NAME: Final[str] = "image-pull-events.jsonl"
DOCKER_DAEMON_FAILURE_MARKERS: Final[tuple[str, ...]] = (
    "connection refused",
    "connection reset by peer",
    "cannot connect to the docker daemon",
    "/run/containerd/containerd.sock",
    "error while dialing",
    "error during connect",
    "unexpected eof",
)
# Transient registry network errors (not daemon failures); retry with longer backoff
REGISTRY_TRANSIENT_FAILURE_MARKERS: Final[tuple[str, ...]] = (
    "context deadline exceeded",
    "request canceled while waiting for connection",
    "client.timeout exceeded while awaiting headers",
    "timeout exceeded while awaiting headers",
    "i/o timeout",
    "tls handshake timeout",
    "temporary failure in name resolution",
    "no such host",
)


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
    compose_files: Sequence[Path], *, cwd: Path, env: dict[str, str]
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
            timeout_seconds=120,
        )
        for raw_line in completed.stdout.splitlines():
            image = raw_line.strip()
            if image and image not in seen:
                seen.add(image)
                images.append(image)
    if not images:
        raise FreshHostError("No compose images were resolved.")
    return images


def list_local_images(images: Sequence[str]) -> list[str]:
    """Return the image refs already present in the local daemon."""
    present: list[str] = []
    for image in images:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            present.append(image)
    return present


def pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
    """Pull one image and return status information."""
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["docker", "pull", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return (
            image,
            1,
            time.monotonic() - started,
            f"docker pull timed out after {timeout_seconds}s",
        )
    output = "\n".join(chunk for chunk in (result.stdout.strip(), result.stderr.strip()) if chunk)
    return image, result.returncode, time.monotonic() - started, output


def _is_daemon_connectivity_failure(output: str) -> bool:
    """Return whether a pull failure output indicates daemon connectivity problems."""
    lowered = output.lower()
    if any(marker in lowered for marker in DOCKER_DAEMON_FAILURE_MARKERS):
        return True
    return "docker.sock" in lowered and "eof" in lowered


def _is_transient_registry_failure(output: str) -> bool:
    """Return whether a pull failure output indicates a transient registry network blip."""
    lowered = output.lower()
    return any(marker in lowered for marker in REGISTRY_TRANSIENT_FAILURE_MARKERS)


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


def _recovery_probe_attempts(deadline: PhaseDeadline | None, clock: Clock) -> int:
    """Return how many readiness probes fit in the remaining budget."""
    if deadline is None:
        return MAX_RECOVERY_PROBES
    fitting = int(deadline.remaining(clock) // READINESS_PROBE_SECONDS)
    return max(1, min(MAX_RECOVERY_PROBES, fitting))


def _budget_allows(deadline: PhaseDeadline | None, clock: Clock, *, spend: float = 0.0) -> bool:
    """Return whether *spend* seconds still leave room for one more pull attempt."""
    if deadline is None:
        return True
    return deadline.remaining(clock) - spend >= MIN_PULL_ATTEMPT_SECONDS


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
    recover_runtime: Callable[[], object] | None = None,
) -> PullReport:
    """Pull images with bounded retries, reduced retry parallelism, and a phase deadline."""
    if parallelism < 1 or max_attempts < 1 or pull_timeout_seconds < 1:
        raise FreshHostError("parallelism, max_attempts, and pull_timeout_seconds must be positive")
    if (recovery_cwd is None) != (recovery_env is None):
        raise FreshHostError("recovery_cwd and recovery_env must be provided together")

    def emit(event: str, **fields: object) -> None:
        if event_log is not None:
            event_log.emit(event, **fields)

    outstanding = list(images)
    pulled_images: list[str] = []
    retried_images: list[str] = []
    seen_retries: set[str] = set()
    attempt_parallelism = parallelism
    attempt_count = 0
    budget_exhausted = False
    while outstanding and attempt_count < max_attempts:
        if not _budget_allows(deadline, clock):
            budget_exhausted = True
            break
        attempt_count += 1
        attempt_timeout = _attempt_timeout_seconds(pull_timeout_seconds, deadline, clock)
        log(
            f"Pulling {len(outstanding)} image(s) with parallelism={attempt_parallelism} "
            f"(attempt {attempt_count}/{max_attempts}, timeout {attempt_timeout}s)."
        )
        emit(
            "attempt_started",
            attempt=attempt_count,
            images=list(outstanding),
            parallelism=attempt_parallelism,
            timeout_seconds=attempt_timeout,
        )
        failures: list[str] = []
        daemon_connectivity_failure = False
        transient_registry_failure = False
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(attempt_parallelism, len(outstanding))
        ) as executor:
            futures = {
                executor.submit(pull_one_image, image, attempt_timeout): image
                for image in outstanding
            }
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
                    image, returncode, duration_seconds, output = future.result()
                    emit(
                        "image_result",
                        attempt=attempt_count,
                        image=image,
                        returncode=returncode,
                        duration_seconds=round(duration_seconds, 3),
                        output_tail=output[-2000:],
                    )
                    if returncode == 0:
                        log(f"[ok] {image} in {duration_seconds:.1f}s")
                        pulled_images.append(image)
                        continue
                    log(f"[failed] {image} in {duration_seconds:.1f}s")
                    if output:
                        print(output, flush=True)
                        if _is_daemon_connectivity_failure(output):
                            daemon_connectivity_failure = True
                        elif _is_transient_registry_failure(output):
                            transient_registry_failure = True
                    failures.append(image)
        outstanding = failures
        if not outstanding or attempt_count >= max_attempts:
            break
        for image in outstanding:
            if image not in seen_retries:
                seen_retries.add(image)
                retried_images.append(image)
        if not _budget_allows(deadline, clock):
            budget_exhausted = True
            break
        if recovery_cwd is not None and recovery_env is not None:
            if daemon_connectivity_failure:
                log("Detected Docker daemon connectivity failure; waiting for runtime recovery.")
            elif transient_registry_failure:
                log("Detected transient registry connectivity failure; backing off before retry.")
            else:
                log("Retrying image pull after failure; probing Docker runtime before retry.")
            try:
                if daemon_connectivity_failure and recover_runtime is not None:
                    # A dead runtime needs a restart, not just a wait (OrbStack on macOS).
                    emit("runtime_recovery", attempt=attempt_count)
                    recover_runtime()
                else:
                    wait_for_docker_ready(
                        cwd=recovery_cwd,
                        env=recovery_env,
                        max_attempts=_recovery_probe_attempts(deadline, clock),
                    )
            except FreshHostError as exc:
                log(f"Docker daemon recovery probe failed: {exc}")
                emit("recovery_failed", attempt=attempt_count, error=str(exc))
                return PullReport(
                    exit_code=1,
                    pulled_images=pulled_images,
                    failed_images=outstanding,
                    attempt_count=attempt_count,
                    retried_images=retried_images,
                    failure_class="daemon_unavailable",
                )
        next_parallelism = max(1, attempt_parallelism // 2)
        if next_parallelism != attempt_parallelism:
            log(f"Reducing retry parallelism {attempt_parallelism}->{next_parallelism}.")
        attempt_parallelism = next_parallelism
        backoff_seconds = _retry_backoff_seconds(
            attempt_count=attempt_count,
            registry_failure=transient_registry_failure and not daemon_connectivity_failure,
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
    return PullReport(
        exit_code=0 if not outstanding else 1,
        pulled_images=pulled_images,
        failed_images=outstanding,
        attempt_count=attempt_count,
        retried_images=retried_images,
        failure_class=BUDGET_EXHAUSTED if budget_exhausted and outstanding else None,
    )


def _failure_class_for(reason: str) -> str:
    """Classify a pre-pull failure message for the ensure report."""
    return BUDGET_EXHAUSTED if reason.startswith(BUDGET_EXHAUSTED) else "ensure_failed"


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
        )

    def persist(report: ImageEnsureReport) -> ImageEnsureReport:
        if report_path is not None:
            write_json(asdict(report), report_path)
        return report

    if not context.ensure_images:
        return persist(build_report(None, None))

    try:
        deadline = pull_phase_deadline(
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
        state["images"] = resolve_compose_images(compose_files, cwd=repo_root, env=env)
        state["local_before"] = list_local_images(state["images"])
        state["missing_before_pull"] = [
            image for image in state["images"] if image not in state["local_before"]
        ]
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
                    (lambda: wait_runtime_ready(context_path))
                    if context.platform == "macos"
                    else None
                ),
            )
        local_after_pull = list_local_images(state["images"])
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
