"""Hosted Docker image cache restore/save helpers for CI workflows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.models import (
    SCENARIO_SPECS,
    FreshHostContext,
    FreshHostError,
    ScenarioId,
)
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.images import (
    compose_probe_env,
    list_local_images,
    pull_images,
    resolve_compose_images,
)
from tests.utils.helpers._hosted_docker.io import log
from tests.utils.helpers._hosted_docker.shell import run_checked

DOCKER_IMAGE_CACHE_ARCHIVE_NAME: Final[str] = "hosted-macos-images.tar"
DOCKER_IMAGE_CACHE_ARCHIVE_TEMPLATE: Final[str] = "hosted-macos-images-{scenario_id}.tar"
DEFAULT_DOCKER_IMAGE_LOAD_TIMEOUT_SECONDS: Final[int] = 3600


def _cache_root() -> Path | None:
    """Resolve the configured Docker image cache root when configured."""
    configured = os.environ.get("FRESH_HOST_DOCKER_IMAGE_CACHE_DIR", "").strip()
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _cache_archive_path(cache_root: Path, *, scenario_id: ScenarioId) -> Path:
    """Return the scenario-specific archive path used for Docker image cache persistence."""
    return cache_root / DOCKER_IMAGE_CACHE_ARCHIVE_TEMPLATE.format(scenario_id=scenario_id)


def _compose_files_for_cache(repo_root: Path, *, platform: str) -> dict[ScenarioId, list[Path]]:
    """Resolve compose files that should participate in cache archives by scenario."""
    if platform != "macos":
        raise FreshHostError("Docker image cache persistence is only supported for macOS contexts")
    compose_files_by_scenario: dict[ScenarioId, list[Path]] = {}
    for scenario_id, spec in SCENARIO_SPECS.items():
        if spec.platform != platform:
            continue
        compose_files: list[Path] = []
        seen: set[Path] = set()
        for relative_path in spec.compose_files:
            compose_file = (repo_root / relative_path).resolve()
            if compose_file in seen:
                continue
            seen.add(compose_file)
            compose_files.append(compose_file)
        compose_files_by_scenario[scenario_id] = compose_files
    return compose_files_by_scenario


def _docker_image_load_timeout_seconds() -> int:
    """Return the timeout used when loading image cache archives into Docker."""
    configured = os.environ.get("FRESH_HOST_DOCKER_IMAGE_LOAD_TIMEOUT_SECONDS", "").strip()
    if not configured:
        return DEFAULT_DOCKER_IMAGE_LOAD_TIMEOUT_SECONDS
    try:
        timeout_seconds = int(configured)
    except ValueError as exc:
        raise FreshHostError(
            "FRESH_HOST_DOCKER_IMAGE_LOAD_TIMEOUT_SECONDS must be an integer"
        ) from exc
    if timeout_seconds < 1:
        raise FreshHostError("FRESH_HOST_DOCKER_IMAGE_LOAD_TIMEOUT_SECONDS must be positive")
    return timeout_seconds


def _archive_candidates(context: FreshHostContext, *, cache_root: Path) -> list[Path]:
    """Return candidate archive paths for restore, preferring scenario-specific archives."""
    scenario_archive = _cache_archive_path(cache_root, scenario_id=context.scenario_id)
    if scenario_archive.name == DOCKER_IMAGE_CACHE_ARCHIVE_NAME:
        return [scenario_archive]
    return [scenario_archive, cache_root / DOCKER_IMAGE_CACHE_ARCHIVE_NAME]


def restore_image_cache(context_path: Path) -> bool:
    """Load a cached Docker image archive into the local daemon when available."""
    context = load_context(context_path)
    cache_root = _cache_root()
    if cache_root is None:
        log("Docker image cache directory is not configured; skipping image cache restore.")
        return False
    archive_path = next(
        (
            candidate
            for candidate in _archive_candidates(context, cache_root=cache_root)
            if candidate.is_file()
        ),
        None,
    )
    if archive_path is None:
        expected_path = _cache_archive_path(cache_root, scenario_id=context.scenario_id)
        log(f"Docker image cache archive was not found at {expected_path}; skipping restore.")
        return False
    repo_root = Path(context.repo_root).resolve()
    run_checked(
        ["docker", "image", "load", "-i", str(archive_path)],
        cwd=repo_root,
        env=dict(os.environ),
        timeout_seconds=_docker_image_load_timeout_seconds(),
    )
    log(f"Loaded Docker image cache from {archive_path}.")
    return True


def save_image_cache(context_path: Path) -> Path:
    """Save one deterministic Docker image archive for hosted macOS workflows."""
    context = load_context(context_path)
    cache_root = _cache_root()
    if cache_root is None:
        raise FreshHostError("FRESH_HOST_DOCKER_IMAGE_CACHE_DIR must be configured")
    cache_root.mkdir(parents=True, exist_ok=True)
    default_archive_path = _cache_archive_path(cache_root, scenario_id=context.scenario_id)

    repo_root = Path(context.repo_root).resolve()
    compose_files_by_scenario = _compose_files_for_cache(repo_root, platform=context.platform)
    env = compose_probe_env(
        context=context,
        repo_root=repo_root,
        compose_state_dir_name="compose-cache-export",
    )
    images_by_scenario: dict[ScenarioId, list[str]] = {}
    all_images: list[str] = []
    seen_images: set[str] = set()
    for scenario_id, compose_files in compose_files_by_scenario.items():
        scenario_images = resolve_compose_images(compose_files, cwd=repo_root, env=env)
        images_by_scenario[scenario_id] = scenario_images
        for image in scenario_images:
            if image in seen_images:
                continue
            seen_images.add(image)
            all_images.append(image)

    local_images = set(list_local_images(all_images))
    missing_images = [image for image in all_images if image not in local_images]
    if missing_images:
        missing_joined = ", ".join(missing_images)
        log(
            "Some compose images are missing locally before cache export; "
            f"attempting pulls: {missing_joined}"
        )
        pull_report = pull_images(
            missing_images,
            parallelism=context.docker_pull_parallelism,
            max_attempts=context.docker_pull_max_attempts,
            recovery_cwd=repo_root,
            recovery_env=env,
        )
        if pull_report.exit_code != 0:
            failed_joined = ", ".join(pull_report.failed_images)
            log(
                "Some compose images are still unavailable after pull attempts; "
                f"continuing with partial cache export: {failed_joined}"
            )
        local_images = set(list_local_images(all_images))

    for scenario_id, scenario_images in images_by_scenario.items():
        images_to_save = [image for image in scenario_images if image in local_images]
        missing_after_pull = [image for image in scenario_images if image not in local_images]
        if missing_after_pull:
            log(
                f"[{scenario_id}] Skipping unavailable images during cache export: "
                + ", ".join(missing_after_pull)
            )
        if not images_to_save:
            log(
                f"[{scenario_id}] No local compose images are available for cache export; skipping."
            )
            continue
        archive_path = _cache_archive_path(cache_root, scenario_id=scenario_id)
        run_checked(
            ["docker", "image", "save", "-o", str(archive_path), *images_to_save],
            cwd=repo_root,
            env=dict(os.environ),
            timeout_seconds=3600,
        )
        log(f"[{scenario_id}] Saved Docker image cache archive to {archive_path}.")
    return default_archive_path
