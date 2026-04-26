"""Compose image discovery and pull orchestration for hosted Docker CI."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import load_env_assignments, varlock_local_env_file
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_json
from tests.utils.helpers._hosted_docker.models import (
    DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    PULL_HEARTBEAT_SECONDS,
    ImageEnsureReport,
    PullReport,
)
from tests.utils.helpers._hosted_docker.shell import run_checked, wait_for_docker_ready

COMPOSE_IMAGE_RESOLUTION_PLACEHOLDER: Final[str] = "compose-image-resolution-placeholder"
DOCKER_DAEMON_FAILURE_MARKERS: Final[tuple[str, ...]] = (
    "connection refused",
    "connection reset by peer",
    "cannot connect to the docker daemon",
    "/run/containerd/containerd.sock",
    "error while dialing",
    "error during connect",
    "unexpected eof",
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


def pull_images(
    images: Sequence[str],
    *,
    parallelism: int,
    max_attempts: int,
    pull_timeout_seconds: int = DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    recovery_cwd: Path | None = None,
    recovery_env: dict[str, str] | None = None,
) -> PullReport:
    """Pull images with bounded retries and reduced retry parallelism."""
    if parallelism < 1 or max_attempts < 1 or pull_timeout_seconds < 1:
        raise FreshHostError("parallelism, max_attempts, and pull_timeout_seconds must be positive")
    if (recovery_cwd is None) != (recovery_env is None):
        raise FreshHostError("recovery_cwd and recovery_env must be provided together")
    outstanding = list(images)
    pulled_images: list[str] = []
    retried_images: list[str] = []
    seen_retries: set[str] = set()
    attempt_parallelism = parallelism
    attempt_count = 0
    while outstanding and attempt_count < max_attempts:
        attempt_count += 1
        log(
            f"Pulling {len(outstanding)} image(s) with parallelism={attempt_parallelism} "
            f"(attempt {attempt_count}/{max_attempts})."
        )
        failures: list[str] = []
        daemon_connectivity_failure = False
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(attempt_parallelism, len(outstanding))
        ) as executor:
            futures = {
                executor.submit(pull_one_image, image, pull_timeout_seconds): image
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
                    if returncode == 0:
                        log(f"[ok] {image} in {duration_seconds:.1f}s")
                        pulled_images.append(image)
                        continue
                    log(f"[failed] {image} in {duration_seconds:.1f}s")
                    if output:
                        print(output, flush=True)
                        if _is_daemon_connectivity_failure(output):
                            daemon_connectivity_failure = True
                    failures.append(image)
        outstanding = failures
        if not outstanding or attempt_count >= max_attempts:
            break
        for image in outstanding:
            if image not in seen_retries:
                seen_retries.add(image)
                retried_images.append(image)
        if recovery_cwd is not None and recovery_env is not None:
            if daemon_connectivity_failure:
                log("Detected Docker daemon connectivity failure; waiting for runtime recovery.")
            else:
                log("Retrying image pull after failure; probing Docker runtime before retry.")
            try:
                wait_for_docker_ready(cwd=recovery_cwd, env=recovery_env, max_attempts=90)
            except FreshHostError as exc:
                log(f"Docker daemon recovery probe failed: {exc}")
                return PullReport(
                    exit_code=1,
                    pulled_images=pulled_images,
                    failed_images=outstanding,
                    attempt_count=attempt_count,
                    retried_images=retried_images,
                )
        next_parallelism = max(1, attempt_parallelism // 2)
        if next_parallelism != attempt_parallelism:
            log(f"Reducing retry parallelism {attempt_parallelism}->{next_parallelism}.")
        attempt_parallelism = next_parallelism
        backoff_seconds = min(10, 2 * attempt_count)
        log(f"Retrying {len(outstanding)} image(s) after {backoff_seconds}s.")
        time.sleep(backoff_seconds)
    return PullReport(
        exit_code=0 if not outstanding else 1,
        pulled_images=pulled_images,
        failed_images=outstanding,
        attempt_count=attempt_count,
        retried_images=retried_images,
    )


def ensure_images(context_path: Path) -> ImageEnsureReport:
    """Ensure the scenario's compose images exist locally."""
    context = load_context(context_path)
    report_path = Path(context.image_report_path).resolve() if context.image_report_path else None
    repo_root = Path(context.repo_root).resolve()
    compose_files = [Path(path).resolve() for path in context.compose_files]
    env = compose_probe_env(
        context=context, repo_root=repo_root, compose_state_dir_name="compose-prepull"
    )
    started_at = now_iso()
    started = time.monotonic()

    if not context.ensure_images:
        finished_at = now_iso()
        report = ImageEnsureReport(
            compose_files=[str(path) for path in compose_files],
            images=[],
            local_before=[],
            missing_before_pull=[],
            pulled_images=[],
            missing_after_pull=[],
            pull_parallelism=context.docker_pull_parallelism,
            pull_attempt_count=0,
            retried_images=[],
            failure_reason=None,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(time.monotonic() - started, 3),
            created_at=finished_at,
        )
        if report_path is not None:
            write_json(asdict(report), report_path)
        return report

    images = resolve_compose_images(compose_files, cwd=repo_root, env=env)
    local_before = list_local_images(images)
    missing_before_pull = [image for image in images if image not in local_before]
    pull_report = None
    if missing_before_pull:
        pull_report = pull_images(
            missing_before_pull,
            parallelism=context.docker_pull_parallelism,
            max_attempts=context.docker_pull_max_attempts,
            recovery_cwd=repo_root,
            recovery_env=env,
        )
    local_after_pull = list_local_images(images)
    missing_after_pull = [image for image in images if image not in local_after_pull]
    failure_reason = None
    if pull_report is not None and pull_report.exit_code != 0:
        failure_reason = "docker pull failed"
    elif missing_after_pull:
        failure_reason = "images remain unavailable after pull"
    finished_at = now_iso()
    report = ImageEnsureReport(
        compose_files=[str(path) for path in compose_files],
        images=images,
        local_before=local_before,
        missing_before_pull=missing_before_pull,
        pulled_images=[] if pull_report is None else list(pull_report.pulled_images),
        missing_after_pull=missing_after_pull,
        pull_parallelism=context.docker_pull_parallelism,
        pull_attempt_count=0 if pull_report is None else pull_report.attempt_count,
        retried_images=[] if pull_report is None else list(pull_report.retried_images),
        failure_reason=failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(time.monotonic() - started, 3),
        created_at=finished_at,
    )
    if report_path is not None:
        write_json(asdict(report), report_path)
    if failure_reason is not None:
        raise FreshHostError(failure_reason)
    return report
