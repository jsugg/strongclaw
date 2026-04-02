"""Hosted Docker image cache restore/save helpers for CI workflows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.models import SCENARIO_SPECS, FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.images import (
    compose_probe_env,
    list_local_images,
    resolve_compose_images,
)
from tests.utils.helpers._hosted_docker.io import log
from tests.utils.helpers._hosted_docker.shell import run_checked

DOCKER_IMAGE_CACHE_ARCHIVE_NAME: Final[str] = "hosted-macos-images.tar"


def _cache_root() -> Path | None:
    """Resolve the configured Docker image cache root when configured."""
    configured = os.environ.get("FRESH_HOST_DOCKER_IMAGE_CACHE_DIR", "").strip()
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _cache_archive_path(cache_root: Path) -> Path:
    """Return the archive path used for Docker image cache persistence."""
    return cache_root / DOCKER_IMAGE_CACHE_ARCHIVE_NAME


def _compose_files_for_cache(repo_root: Path, *, platform: str) -> list[Path]:
    """Resolve compose files that should participate in one cache archive."""
    if platform != "macos":
        raise FreshHostError("Docker image cache persistence is only supported for macOS contexts")
    compose_files: list[Path] = []
    seen: set[Path] = set()
    for spec in SCENARIO_SPECS.values():
        if spec.platform != platform:
            continue
        for relative_path in spec.compose_files:
            compose_file = (repo_root / relative_path).resolve()
            if compose_file in seen:
                continue
            seen.add(compose_file)
            compose_files.append(compose_file)
    return compose_files


def restore_image_cache(context_path: Path) -> bool:
    """Load a cached Docker image archive into the local daemon when available."""
    context = load_context(context_path)
    cache_root = _cache_root()
    if cache_root is None:
        log("Docker image cache directory is not configured; skipping image cache restore.")
        return False
    archive_path = _cache_archive_path(cache_root)
    if not archive_path.is_file():
        log(f"Docker image cache archive was not found at {archive_path}; skipping restore.")
        return False
    repo_root = Path(context.repo_root).resolve()
    run_checked(
        ["docker", "image", "load", "-i", str(archive_path)],
        cwd=repo_root,
        env=dict(os.environ),
        timeout_seconds=1800,
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
    archive_path = _cache_archive_path(cache_root)

    repo_root = Path(context.repo_root).resolve()
    compose_files = _compose_files_for_cache(repo_root, platform=context.platform)
    env = compose_probe_env(
        context=context,
        repo_root=repo_root,
        compose_state_dir_name="compose-cache-export",
    )
    images = resolve_compose_images(compose_files, cwd=repo_root, env=env)
    local_images = set(list_local_images(images))
    missing_images = [image for image in images if image not in local_images]
    if missing_images:
        missing_joined = ", ".join(missing_images)
        raise FreshHostError(
            "Cannot save Docker image cache because some compose images are missing locally: "
            f"{missing_joined}"
        )

    run_checked(
        ["docker", "image", "save", "-o", str(archive_path), *images],
        cwd=repo_root,
        env=dict(os.environ),
        timeout_seconds=3600,
    )
    log(f"Saved Docker image cache archive to {archive_path}.")
    return archive_path
