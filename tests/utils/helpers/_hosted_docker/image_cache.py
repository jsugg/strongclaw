"""Docker image cache save/restore helpers for hosted macOS CI workflows."""

from __future__ import annotations

import os
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context
from tests.utils.helpers._hosted_docker.images import (
    compose_probe_env,
    list_local_images,
    resolve_compose_images,
)
from tests.utils.helpers._hosted_docker.io import log
from tests.utils.helpers._hosted_docker.shell import run_checked


def save_image_cache(context_path: Path, output_path: Path) -> None:
    """Save the current scenario's compose images to an uncompressed tar archive.

    Saves only images that are present in the local daemon.  The archive is
    written to *output_path*; the parent directory is created if needed.
    Raises :class:`FreshHostError` if no images are locally available.
    """
    context = load_context(context_path)
    repo_root = Path(context.repo_root).resolve()
    compose_files = [Path(path).resolve() for path in context.compose_files]
    env = compose_probe_env(
        context=context,
        repo_root=repo_root,
        compose_state_dir_name="compose-save-cache",
    )
    images = resolve_compose_images(compose_files, cwd=repo_root, env=env)
    local_images = list_local_images(images)
    if not local_images:
        raise FreshHostError(
            "No locally-available compose images found; cannot save Docker image cache."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        ["docker", "image", "save", "-o", str(output_path), *local_images],
        cwd=repo_root,
        env=dict(os.environ),
        timeout_seconds=3600,
    )
    size_mb = output_path.stat().st_size // (1024 * 1024)
    log(
        f"Saved {len(local_images)} image(s) to {output_path} ({size_mb} MB). "
        f"Images: {', '.join(local_images)}"
    )


def restore_image_cache(context_path: Path, archive_path: Path) -> bool:
    """Load a Docker image archive into the local daemon.

    Returns *True* when the archive was successfully loaded, *False* when the
    archive file does not exist (treated as a non-fatal cache miss).
    Raises :class:`FreshHostError` on load failure.
    """
    context = load_context(context_path)
    if not archive_path.is_file():
        log(f"Docker image cache archive not found at {archive_path}; skipping restore.")
        return False
    repo_root = Path(context.repo_root).resolve()
    log(f"Loading Docker image cache from {archive_path} …")
    run_checked(
        ["docker", "image", "load", "-i", str(archive_path)],
        cwd=repo_root,
        env=dict(os.environ),
        timeout_seconds=3600,
    )
    log("Docker image cache loaded successfully.")
    return True
