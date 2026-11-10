"""Reset the derived Qdrant index when its storage belongs to a different Qdrant release line.

Hypermemory keeps canonical memory in Markdown; Qdrant only holds a derived index that a
reindex rebuilds. Qdrant itself only supports upgrading one minor release at a time, and a
release that has dropped an old storage format refuses to start on it. Rather than stepping
through every intermediate release, StrongClaw stamps which release line owns the storage,
resets that storage when the pinned image moves to another major.minor, and reindexes.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Final, Literal, cast

QDRANT_SERVICE_NAME: Final[str] = "qdrant"
QDRANT_STORAGE_TARGET: Final[str] = "/qdrant/storage"
QDRANT_STATE_COMPONENT: Final[str] = "qdrant"
STORAGE_STAMP_FILE_NAME: Final[str] = "qdrant.storage-version.json"
UNKNOWN_RELEASE_LINE: Final[str] = "unknown"
_STAMP_SCHEMA_VERSION: Final[int] = 1
_IMAGE_RELEASE: Final[re.Pattern[str]] = re.compile(
    r":v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:@|$)"
)

type StorageAction = Literal["none", "reset"]
type MountKind = Literal["bind", "volume"]
type ReindexOutcome = Literal["not-needed", "reindexed", "skipped-no-hypermemory", "deferred"]


class QdrantStorageError(RuntimeError):
    """Raised when Qdrant storage cannot be inspected or safely reset."""


@dataclasses.dataclass(frozen=True, slots=True)
class QdrantRelease:
    """One Qdrant release, parsed from a pinned image reference."""

    major: int
    minor: int
    patch: int

    @property
    def line(self) -> str:
        """Return the major.minor release line that determines storage compatibility."""
        return f"{self.major}.{self.minor}"

    @classmethod
    def from_image(cls, image: str) -> QdrantRelease:
        """Parse `qdrant/qdrant:v1.20.0@sha256:...` style image references."""
        match = _IMAGE_RELEASE.search(image)
        if match is None:
            raise QdrantStorageError(f"cannot determine the Qdrant release from image {image!r}")
        return cls(int(match["major"]), int(match["minor"]), int(match["patch"]))


@dataclasses.dataclass(frozen=True, slots=True)
class QdrantStorageMount:
    """Where the Qdrant service keeps its storage, as resolved by `docker compose config`."""

    kind: MountKind
    source: str


@dataclasses.dataclass(frozen=True, slots=True)
class StorageStamp:
    """Record of the Qdrant release line that owns the storage."""

    release_line: str
    image: str
    reindex_pending: bool
    updated_at: str

    def to_json(self) -> str:
        """Serialize the stamp."""
        payload = {"schema": _STAMP_SCHEMA_VERSION, **dataclasses.asdict(self)}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


@dataclasses.dataclass(frozen=True, slots=True)
class StorageDecision:
    """What to do with existing Qdrant storage before the service starts."""

    action: StorageAction
    reason: str
    target_line: str
    stamped_line: str | None


def _mapping(value: object, *, path: str) -> dict[str, object]:
    """Validate one JSON object from compose config output."""
    if not isinstance(value, dict):
        raise QdrantStorageError(f"compose config {path} must be an object")
    return {
        str(key): item
        for key, item in cast(dict[object, object], value).items()
        if isinstance(key, str)
    }


def _qdrant_service(compose_config: Mapping[str, object]) -> dict[str, object]:
    services = _mapping(compose_config.get("services"), path="services")
    return _mapping(services.get(QDRANT_SERVICE_NAME), path=f"services.{QDRANT_SERVICE_NAME}")


def resolve_target_release(compose_config: Mapping[str, object]) -> tuple[QdrantRelease, str]:
    """Return the pinned Qdrant release and image from compose config JSON."""
    image = _qdrant_service(compose_config).get("image")
    if not isinstance(image, str) or not image:
        raise QdrantStorageError("compose config does not pin an image for the qdrant service")
    return QdrantRelease.from_image(image), image


def resolve_storage_mount(compose_config: Mapping[str, object]) -> QdrantStorageMount:
    """Return the mount backing `/qdrant/storage` from compose config JSON."""
    volumes = _qdrant_service(compose_config).get("volumes")
    if not isinstance(volumes, list):
        raise QdrantStorageError("the qdrant service declares no volumes")
    for raw in cast(list[object], volumes):
        mount = _mapping(raw, path="services.qdrant.volumes[]")
        if mount.get("target") != QDRANT_STORAGE_TARGET:
            continue
        kind = mount.get("type")
        source = mount.get("source")
        if kind not in ("bind", "volume") or not isinstance(source, str) or not source:
            raise QdrantStorageError(f"unsupported qdrant storage mount: {mount!r}")
        mount_kind: MountKind = "bind" if kind == "bind" else "volume"
        return QdrantStorageMount(kind=mount_kind, source=source)
    raise QdrantStorageError(f"the qdrant service does not mount {QDRANT_STORAGE_TARGET}")


def stamp_path(compose_state_dir: pathlib.Path) -> pathlib.Path:
    """Return the stamp location, beside (not inside) the Qdrant storage directory."""
    return compose_state_dir / STORAGE_STAMP_FILE_NAME


def read_stamp(path: pathlib.Path) -> StorageStamp | None:
    """Read the stamp; a missing or unreadable stamp means the owning release is unknown."""
    try:
        payload = _mapping(json.loads(path.read_text(encoding="utf-8")), path="stamp")
    except (OSError, ValueError, QdrantStorageError):
        return None
    release_line = payload.get("release_line")
    image = payload.get("image")
    reindex_pending = payload.get("reindex_pending")
    updated_at = payload.get("updated_at")
    if not (
        isinstance(release_line, str)
        and isinstance(image, str)
        and isinstance(reindex_pending, bool)
        and isinstance(updated_at, str)
    ):
        return None
    return StorageStamp(release_line, image, reindex_pending, updated_at)


def write_stamp(path: pathlib.Path, stamp: StorageStamp) -> None:
    """Write the stamp atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(stamp.to_json(), encoding="utf-8")
    staging.replace(path)


def _now() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


def new_stamp(release: QdrantRelease, image: str, *, reindex_pending: bool) -> StorageStamp:
    """Return a stamp for *release* recorded now."""
    return StorageStamp(
        release_line=release.line, image=image, reindex_pending=reindex_pending, updated_at=_now()
    )


def reset_intent_stamp(stamp: StorageStamp | None) -> StorageStamp:
    """Return the stamp recorded before a reset deletes anything.

    It keeps the previous owner's release line, so an interrupted reset is retried while storage
    remains, and marks the reindex pending, so it is not lost once storage is already gone.
    """
    if stamp is None:
        return StorageStamp(UNKNOWN_RELEASE_LINE, "", True, _now())
    return dataclasses.replace(stamp, reindex_pending=True, updated_at=_now())


def decide_storage_action(
    *, target: QdrantRelease, stamp: StorageStamp | None, storage_present: bool
) -> StorageDecision:
    """Decide whether existing storage must be reset before the pinned release starts."""
    stamped = None if stamp is None else stamp.release_line
    if not storage_present:
        return StorageDecision("none", "no existing qdrant storage", target.line, stamped)
    if stamp is None:
        # Every release shipped before the stamp existed (1.15.x) predates this storage format.
        return StorageDecision(
            "reset", "existing qdrant storage has no release stamp", target.line, None
        )
    if stamp.release_line != target.line:
        return StorageDecision(
            "reset",
            f"qdrant storage belongs to release line {stamp.release_line}, "
            f"pinned image is {target.line}",
            target.line,
            stamped,
        )
    return StorageDecision(
        "none", "qdrant storage matches the pinned release", target.line, stamped
    )


def guarded_storage_dir(
    mount: QdrantStorageMount, *, compose_state_dir: pathlib.Path
) -> pathlib.Path:
    """Return the bind-mounted storage directory, refusing anything but `<state>/qdrant`."""
    if mount.kind != "bind":
        raise QdrantStorageError(f"qdrant storage is a {mount.kind} mount, not a directory")
    source = pathlib.Path(mount.source)
    expected = compose_state_dir.expanduser().resolve() / QDRANT_STATE_COMPONENT
    if not source.is_absolute():
        raise QdrantStorageError(f"refusing to reset relative qdrant storage path {source}")
    if source.is_symlink():
        raise QdrantStorageError(f"refusing to reset symlinked qdrant storage path {source}")
    if source.resolve() != expected:
        raise QdrantStorageError(
            f"refusing to reset {source}: it is not the qdrant storage directory {expected}"
        )
    return expected


def clear_directory(directory: pathlib.Path) -> None:
    """Remove everything inside *directory*, never following symlinks out of it."""
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def guarded_volume_name(mount: QdrantStorageMount, *, compose_config: Mapping[str, object]) -> str:
    """Return the compose-managed volume backing qdrant storage, refusing external volumes."""
    if mount.kind != "volume":
        raise QdrantStorageError(f"qdrant storage is a {mount.kind} mount, not a volume")
    volumes = _mapping(compose_config.get("volumes"), path="volumes")
    entry = _mapping(volumes.get(mount.source), path=f"volumes.{mount.source}")
    if entry.get("external"):
        raise QdrantStorageError(
            f"refusing to reset external volume {mount.source}; reset it manually"
        )
    name = entry.get("name")
    project = compose_config.get("name")
    if not isinstance(name, str) or not isinstance(project, str):
        raise QdrantStorageError(f"compose config does not name volume {mount.source}")
    if name != f"{project}_{mount.source}":
        raise QdrantStorageError(
            f"refusing to reset volume {name}: it is not this project's {mount.source} volume"
        )
    return name


@dataclasses.dataclass(frozen=True, slots=True)
class StorageOperations:
    """Side effects the upgrade needs, injected so the decision path stays testable."""

    volume_exists: Callable[[str], bool]
    stop_service: Callable[[], None]
    remove_volume: Callable[[str], None]
    log: Callable[[str], None]


def prepare_storage(
    *,
    compose_config: Mapping[str, object],
    compose_state_dir: pathlib.Path,
    operations: StorageOperations,
) -> StorageDecision:
    """Reset mismatched Qdrant storage before the service starts; idempotent when it matches."""
    target, image = resolve_target_release(compose_config)
    mount = resolve_storage_mount(compose_config)
    stamp_file = stamp_path(compose_state_dir)
    stamp = read_stamp(stamp_file)
    if mount.kind == "bind":
        storage_dir = guarded_storage_dir(mount, compose_state_dir=compose_state_dir)
        present = storage_dir.is_dir() and any(storage_dir.iterdir())
        volume_name = None
    else:
        volume_name = guarded_volume_name(mount, compose_config=compose_config)
        present = operations.volume_exists(volume_name)
        storage_dir = None
    decision = decide_storage_action(target=target, stamp=stamp, storage_present=present)
    if decision.action == "reset":
        location = str(storage_dir) if storage_dir is not None else f"volume {volume_name}"
        operations.log(
            f"Resetting qdrant storage at {location} ({decision.reason}); hypermemory will be "
            "reindexed from its Markdown source of truth."
        )
        operations.stop_service()
        write_stamp(stamp_file, reset_intent_stamp(stamp))
        if storage_dir is not None:
            clear_directory(storage_dir)
        elif volume_name is not None:
            operations.remove_volume(volume_name)
        write_stamp(stamp_file, new_stamp(target, image, reindex_pending=True))
    elif stamp is None or stamp.release_line != target.line:
        # Storage is gone; keep a reindex an interrupted reset recorded before deleting it.
        pending = stamp is not None and stamp.reindex_pending
        write_stamp(stamp_file, new_stamp(target, image, reindex_pending=pending))
    return decision


def complete_reindex(
    *,
    compose_state_dir: pathlib.Path,
    qdrant_ready: bool,
    reindex: Callable[[], bool],
) -> ReindexOutcome:
    """Run the pending post-reset reindex once Qdrant is healthy, then clear the marker.

    *reindex* is only called when a reindex is pending; it returns False when nothing consumes
    Qdrant (hypermemory disabled), so the marker is cleared without indexing.
    """
    stamp_file = stamp_path(compose_state_dir)
    stamp = read_stamp(stamp_file)
    if stamp is None or not stamp.reindex_pending:
        return "not-needed"
    if not qdrant_ready:
        return "deferred"
    outcome: ReindexOutcome = "reindexed" if reindex() else "skipped-no-hypermemory"
    write_stamp(stamp_file, dataclasses.replace(stamp, reindex_pending=False))
    return outcome
