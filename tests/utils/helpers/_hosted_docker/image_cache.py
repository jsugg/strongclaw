"""Hosted Docker image cache restore/save helpers for CI workflows."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Final, cast

from tests.utils.helpers._fresh_host.models import (
    SCENARIO_SPECS,
    FreshHostContext,
    FreshHostError,
    ScenarioId,
)
from tests.utils.helpers._fresh_host.storage import load_context, now_iso
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
DOCKER_IMAGE_CACHE_MANIFEST_TEMPLATE: Final[str] = "hosted-macos-images-{scenario_id}.manifest.json"
DOCKER_IMAGE_CACHE_MANIFEST_SCHEMA_VERSION: Final[str] = "1.0.0"
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


def _cache_manifest_path(cache_root: Path, *, scenario_id: ScenarioId) -> Path:
    """Return the scenario-specific manifest path used for cache metadata."""
    return cache_root / DOCKER_IMAGE_CACHE_MANIFEST_TEMPLATE.format(scenario_id=scenario_id)


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


def _compose_hash(compose_files: list[Path]) -> str:
    """Compute a deterministic hash for the scenario compose surface."""
    digest = hashlib.sha256()
    for compose_file in sorted(compose_files, key=lambda path: path.as_posix()):
        digest.update(compose_file.as_posix().encode("utf-8"))
        digest.update(b"\0")
        if compose_file.is_file():
            digest.update(compose_file.read_bytes())
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_versions() -> dict[str, str]:
    """Capture runtime tool versions used when creating cache archives."""
    versions: dict[str, str] = {}
    for env_name in ("MACOS_COLIMA_VERSION", "MACOS_LIMA_VERSION"):
        value = os.environ.get(env_name, "").strip()
        if value:
            versions[env_name] = value
    return versions


def _schema_major(schema_version: str) -> int:
    """Extract the major schema version from a semantic-ish version string."""
    try:
        return int(schema_version.split(".", 1)[0])
    except (IndexError, ValueError) as exc:
        raise FreshHostError(f"invalid manifest schema_version: {schema_version!r}") from exc


def _load_manifest(path: Path) -> dict[str, object]:
    """Load and validate one cache manifest payload."""
    try:
        payload_object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FreshHostError(f"invalid cache manifest JSON at {path}: {exc}") from exc
    if not isinstance(payload_object, dict):
        raise FreshHostError(f"cache manifest at {path} must be a JSON object")
    payload = cast(dict[object, object], payload_object)
    validated: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise FreshHostError(f"cache manifest at {path} must use string keys")
        validated[key] = value
    return validated


def _manifest_is_compatible(
    manifest: dict[str, object],
    *,
    scenario_id: ScenarioId,
    compose_hash: str,
) -> bool:
    """Return whether one manifest can be used for the requested scenario."""
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, str):
        log("Cache manifest is missing string field schema_version; skipping restore candidate.")
        return False
    try:
        current_major = _schema_major(DOCKER_IMAGE_CACHE_MANIFEST_SCHEMA_VERSION)
        manifest_major = _schema_major(schema_version)
    except FreshHostError as exc:
        log(str(exc))
        return False
    if manifest_major != current_major:
        log(
            "Cache manifest schema major version mismatch; "
            f"expected {current_major}, got {manifest_major}. Skipping restore candidate."
        )
        return False

    if manifest.get("scenario_id") != scenario_id:
        log(
            "Cache manifest scenario mismatch; "
            f"expected {scenario_id!r}, got {manifest.get('scenario_id')!r}. "
            "Skipping restore candidate."
        )
        return False

    if manifest.get("compose_hash") != compose_hash:
        log(
            "Cache manifest compose hash mismatch; "
            "skipping restore candidate to avoid cross-surface cache promotion."
        )
        return False

    runtime_versions_value = manifest.get("runtime_versions")
    if isinstance(runtime_versions_value, dict):
        runtime_versions = cast(dict[object, object], runtime_versions_value)
        expected_versions = _runtime_versions()
        for key, expected_value in expected_versions.items():
            current_value = runtime_versions.get(key)
            if current_value is not None and not isinstance(current_value, str):
                log(
                    f"Cache manifest runtime version for {key} was not a string. "
                    "Skipping restore candidate."
                )
                return False
            if isinstance(current_value, str) and current_value != expected_value:
                log(
                    "Cache manifest runtime version mismatch for "
                    f"{key}: expected {expected_value!r}, got {current_value!r}. "
                    "Skipping restore candidate."
                )
                return False
    return True


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
    repo_root = Path(context.repo_root).resolve()
    compose_files_by_scenario = _compose_files_for_cache(repo_root, platform=context.platform)
    scenario_compose_files = compose_files_by_scenario.get(context.scenario_id, [])
    compose_hash = _compose_hash(scenario_compose_files) if scenario_compose_files else ""
    require_manifest = (
        os.environ.get("FRESH_HOST_PROMOTION_MANIFEST_REQUIRED", "").strip().lower() == "true"
    )

    archive_path: Path | None = None
    for candidate in _archive_candidates(context, cache_root=cache_root):
        if not candidate.is_file():
            continue
        is_legacy_fallback = candidate.name == DOCKER_IMAGE_CACHE_ARCHIVE_NAME
        if is_legacy_fallback:
            log(
                "Using legacy Docker image cache archive fallback "
                f"{candidate}. Scenario-specific archive was unavailable or invalid."
            )
            archive_path = candidate
            break
        manifest_path = _cache_manifest_path(cache_root, scenario_id=context.scenario_id)
        if not manifest_path.is_file():
            log(
                "Scenario-specific Docker image cache manifest was not found at "
                f"{manifest_path}; skipping scenario archive restore."
            )
            if require_manifest:
                continue
            archive_path = candidate
            break
        try:
            manifest = _load_manifest(manifest_path)
        except FreshHostError as exc:
            log(str(exc))
            if require_manifest:
                continue
            archive_path = candidate
            break
        if _manifest_is_compatible(
            manifest,
            scenario_id=context.scenario_id,
            compose_hash=compose_hash,
        ):
            archive_path = candidate
            break
        if not require_manifest:
            archive_path = candidate
            break

    if archive_path is None:
        expected_path = _cache_archive_path(cache_root, scenario_id=context.scenario_id)
        log(f"Docker image cache archive was not found at {expected_path}; skipping restore.")
        return False
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
        compose_hash = _compose_hash(compose_files_by_scenario[scenario_id])
        manifest_path = _cache_manifest_path(cache_root, scenario_id=scenario_id)
        manifest_payload = {
            "schema_version": DOCKER_IMAGE_CACHE_MANIFEST_SCHEMA_VERSION,
            "scenario_id": scenario_id,
            "compose_hash": compose_hash,
            "runtime_versions": _runtime_versions(),
            "image_digests": images_to_save,
            "created_at": now_iso(),
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        log(f"[{scenario_id}] Saved Docker image cache archive to {archive_path}.")
    return default_archive_path
