"""Unit tests for the Qdrant storage-upgrade decision, guards, and reset path."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import pathlib

import pytest

from clawops import strongclaw_ops
from clawops.qdrant_storage import (
    UNKNOWN_RELEASE_LINE,
    QdrantRelease,
    QdrantStorageError,
    QdrantStorageMount,
    StorageOperations,
    StorageStamp,
    clear_directory,
    complete_reindex,
    decide_storage_action,
    guarded_storage_dir,
    guarded_volume_name,
    new_stamp,
    prepare_storage,
    read_stamp,
    reset_intent_stamp,
    resolve_storage_mount,
    resolve_target_release,
    stamp_path,
    write_stamp,
)
from clawops.strongclaw_runtime import CommandError
from tests.plugins.infrastructure.context import TestContext

_IMAGE = "qdrant/qdrant:v1.20.0@sha256:" + "a" * 64
_TARGET = QdrantRelease(1, 20, 0)
_PROJECT = "strongclaw-sidecars-unit"


class _RecordingOperations:
    """Record every side effect requested by `prepare_storage`."""

    def __init__(
        self, *, existing_volumes: tuple[str, ...] = (), remove_error: Exception | None = None
    ) -> None:
        self.existing_volumes = set(existing_volumes)
        self.remove_error = remove_error
        self.calls: list[str] = []
        self.messages: list[str] = []

    def build(self) -> StorageOperations:
        return StorageOperations(
            volume_exists=self._volume_exists,
            stop_service=lambda: self.calls.append("stop"),
            remove_volume=self._remove_volume,
            log=self.messages.append,
        )

    def _volume_exists(self, name: str) -> bool:
        self.calls.append(f"inspect:{name}")
        return name in self.existing_volumes

    def _remove_volume(self, name: str) -> None:
        self.calls.append(f"remove:{name}")
        if self.remove_error is not None:
            raise self.remove_error
        self.existing_volumes.discard(name)


def _bind_config(source: pathlib.Path, *, image: str = _IMAGE) -> dict[str, object]:
    return {
        "name": _PROJECT,
        "services": {
            "qdrant": {
                "image": image,
                "volumes": [{"type": "bind", "source": str(source), "target": "/qdrant/storage"}],
            }
        },
    }


def _volume_config(
    *, volume_name: str = f"{_PROJECT}_qdrant-data", external: bool = False
) -> dict[str, object]:
    entry: dict[str, object] = {"name": volume_name}
    if external:
        entry["external"] = True
    return {
        "name": _PROJECT,
        "services": {
            "qdrant": {
                "image": _IMAGE,
                "volumes": [
                    {"type": "volume", "source": "qdrant-data", "target": "/qdrant/storage"}
                ],
            }
        },
        "volumes": {"qdrant-data": entry},
    }


def _stamp(line: str, *, pending: bool = False) -> StorageStamp:
    return StorageStamp(line, f"qdrant/qdrant:v{line}.0", pending, "2026-01-01T00:00:00+00:00")


def _state_with_storage(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    state_dir = tmp_path / "compose-state"
    storage_dir = state_dir / "qdrant"
    (storage_dir / "collections" / "memory").mkdir(parents=True)
    (storage_dir / "collections" / "memory" / "segment.bin").write_bytes(b"legacy")
    (storage_dir / "raft_state.json").write_text("{}", encoding="utf-8")
    return state_dir, storage_dir


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("qdrant/qdrant:v1.20.0@sha256:" + "b" * 64, QdrantRelease(1, 20, 0)),
        ("ghcr.io/qdrant/qdrant/qdrant:v1.15.5", QdrantRelease(1, 15, 5)),
        ("qdrant/qdrant:2.0.1", QdrantRelease(2, 0, 1)),
    ],
)
def test_release_is_parsed_from_pinned_image(image: str, expected: QdrantRelease) -> None:
    assert QdrantRelease.from_image(image) == expected
    assert resolve_target_release(_bind_config(pathlib.Path("/s"), image=image)) == (
        expected,
        image,
    )


@pytest.mark.parametrize("image", ["qdrant/qdrant:latest", "qdrant/qdrant:v1.20.0-rc1", ""])
def test_unpinned_image_is_rejected(image: str) -> None:
    with pytest.raises(QdrantStorageError):
        resolve_target_release(_bind_config(pathlib.Path("/s"), image=image))


def test_storage_mount_resolves_bind_and_volume(tmp_path: pathlib.Path) -> None:
    assert resolve_storage_mount(_bind_config(tmp_path)) == QdrantStorageMount(
        "bind", str(tmp_path)
    )
    assert resolve_storage_mount(_volume_config()) == QdrantStorageMount("volume", "qdrant-data")


@pytest.mark.parametrize(
    "volumes",
    [
        None,
        [{"type": "bind", "source": "/x", "target": "/elsewhere"}],
        [{"type": "tmpfs", "target": "/qdrant/storage"}],
        [{"type": "bind", "source": "", "target": "/qdrant/storage"}],
    ],
)
def test_storage_mount_rejects_unusable_definitions(volumes: list[dict[str, str]] | None) -> None:
    config = {"services": {"qdrant": {"image": _IMAGE, "volumes": volumes}}}
    with pytest.raises(QdrantStorageError):
        resolve_storage_mount(config)


@pytest.mark.parametrize(
    ("stamp", "present", "action"),
    [
        (None, False, "none"),
        (_stamp("1.15"), False, "none"),
        (None, True, "reset"),
        (_stamp("1.15"), True, "reset"),
        (_stamp("2.20"), True, "reset"),
        (_stamp("1.20"), True, "none"),
    ],
)
def test_decision_table(stamp: StorageStamp | None, present: bool, action: str) -> None:
    decision = decide_storage_action(target=_TARGET, stamp=stamp, storage_present=present)
    assert decision.action == action
    assert decision.target_line == "1.20"


def test_patch_releases_share_a_storage_line() -> None:
    decision = decide_storage_action(
        target=QdrantRelease(1, 20, 7), stamp=_stamp("1.20"), storage_present=True
    )
    assert decision.action == "none"


def test_stamp_round_trips_and_rejects_corruption(tmp_path: pathlib.Path) -> None:
    path = stamp_path(tmp_path)
    assert read_stamp(path) is None
    stamp = new_stamp(_TARGET, _IMAGE, reindex_pending=True)
    write_stamp(path, stamp)
    assert read_stamp(path) == stamp
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    for corrupt in ("not json", "[]", json.dumps({"release_line": "1.20"})):
        path.write_text(corrupt, encoding="utf-8")
        assert read_stamp(path) is None


def test_bind_guard_accepts_only_the_state_qdrant_directory(tmp_path: pathlib.Path) -> None:
    state_dir = tmp_path / "state"
    (state_dir / "qdrant").mkdir(parents=True)
    mount = QdrantStorageMount("bind", str(state_dir / "qdrant"))
    assert guarded_storage_dir(mount, compose_state_dir=state_dir) == (
        state_dir.resolve() / "qdrant"
    )


@pytest.mark.parametrize(
    "source",
    ["state", "state/postgres", "state/qdrant/../postgres", "elsewhere/qdrant", "state/qdrant/x"],
)
def test_bind_guard_refuses_paths_outside_qdrant_storage(
    tmp_path: pathlib.Path, source: str
) -> None:
    state_dir = tmp_path / "state"
    (state_dir / "qdrant").mkdir(parents=True)
    with pytest.raises(QdrantStorageError, match="refusing"):
        guarded_storage_dir(
            QdrantStorageMount("bind", str(tmp_path / source)), compose_state_dir=state_dir
        )


def test_bind_guard_refuses_relative_and_symlinked_sources(tmp_path: pathlib.Path) -> None:
    state_dir = tmp_path / "state"
    outside = tmp_path / "markdown-memory"
    outside.mkdir()
    state_dir.mkdir()
    (state_dir / "qdrant").symlink_to(outside, target_is_directory=True)
    with pytest.raises(QdrantStorageError, match="relative"):
        guarded_storage_dir(QdrantStorageMount("bind", "state/qdrant"), compose_state_dir=state_dir)
    with pytest.raises(QdrantStorageError, match="symlinked"):
        guarded_storage_dir(
            QdrantStorageMount("bind", str(state_dir / "qdrant")), compose_state_dir=state_dir
        )
    with pytest.raises(QdrantStorageError, match="not a directory"):
        guarded_storage_dir(
            QdrantStorageMount("volume", "qdrant-data"), compose_state_dir=state_dir
        )


def test_volume_guard_accepts_only_this_projects_managed_volume() -> None:
    mount = QdrantStorageMount("volume", "qdrant-data")
    assert guarded_volume_name(mount, compose_config=_volume_config()) == (
        f"{_PROJECT}_qdrant-data"
    )
    with pytest.raises(QdrantStorageError, match="external"):
        guarded_volume_name(mount, compose_config=_volume_config(external=True))
    with pytest.raises(QdrantStorageError, match="not this project's"):
        guarded_volume_name(mount, compose_config=_volume_config(volume_name="shared_data"))
    with pytest.raises(QdrantStorageError, match="not a volume"):
        guarded_volume_name(QdrantStorageMount("bind", "/x"), compose_config=_volume_config())


def test_clear_directory_unlinks_symlinks_without_following_them(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "storage"
    outside = tmp_path / "outside"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "file").write_text("x", encoding="utf-8")
    outside.mkdir()
    (outside / "keep.md").write_text("canonical", encoding="utf-8")
    (target / "dir-link").symlink_to(outside, target_is_directory=True)
    (target / "file-link").symlink_to(outside / "keep.md")
    clear_directory(target)
    assert target.is_dir() and not any(target.iterdir())
    assert (outside / "keep.md").read_text(encoding="utf-8") == "canonical"
    clear_directory(tmp_path / "missing")


def test_bind_legacy_storage_is_reset_once_and_siblings_survive(tmp_path: pathlib.Path) -> None:
    state_dir, storage_dir = _state_with_storage(tmp_path)
    (state_dir / "postgres").mkdir()
    (state_dir / "postgres" / "PG_VERSION").write_text("16", encoding="utf-8")
    memory = state_dir / "MEMORY.md"
    memory.write_text("# canonical\n", encoding="utf-8")
    recorder = _RecordingOperations()

    decision = prepare_storage(
        compose_config=_bind_config(storage_dir),
        compose_state_dir=state_dir,
        operations=recorder.build(),
    )

    assert decision.action == "reset" and decision.stamped_line is None
    assert recorder.calls == ["stop"]
    assert str(storage_dir.resolve()) in recorder.messages[0]
    assert storage_dir.is_dir() and not any(storage_dir.iterdir())
    assert (state_dir / "postgres" / "PG_VERSION").read_text(encoding="utf-8") == "16"
    assert memory.read_text(encoding="utf-8") == "# canonical\n"
    stamp = read_stamp(stamp_path(state_dir))
    assert stamp is not None and stamp.release_line == "1.20" and stamp.reindex_pending

    (storage_dir / "collections").mkdir()
    again = prepare_storage(
        compose_config=_bind_config(storage_dir),
        compose_state_dir=state_dir,
        operations=recorder.build(),
    )
    assert again.action == "none"
    assert recorder.calls == ["stop"]
    assert (storage_dir / "collections").is_dir()


def test_bind_fresh_install_stamps_without_reset(tmp_path: pathlib.Path) -> None:
    state_dir = tmp_path / "compose-state"
    recorder = _RecordingOperations()
    decision = prepare_storage(
        compose_config=_bind_config(state_dir / "qdrant"),
        compose_state_dir=state_dir,
        operations=recorder.build(),
    )
    assert decision.action == "none"
    assert recorder.calls == [] and recorder.messages == []
    stamp = read_stamp(stamp_path(state_dir))
    assert stamp is not None and not stamp.reindex_pending


def test_bind_guard_failure_happens_before_any_side_effect(tmp_path: pathlib.Path) -> None:
    state_dir, _ = _state_with_storage(tmp_path)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "data").write_text("x", encoding="utf-8")
    recorder = _RecordingOperations()
    with pytest.raises(QdrantStorageError):
        prepare_storage(
            compose_config=_bind_config(precious),
            compose_state_dir=state_dir,
            operations=recorder.build(),
        )
    assert recorder.calls == []
    assert (precious / "data").exists()
    assert not stamp_path(state_dir).exists()


def test_volume_mismatch_removes_only_the_project_volume(tmp_path: pathlib.Path) -> None:
    volume = f"{_PROJECT}_qdrant-data"
    write_stamp(stamp_path(tmp_path), _stamp("1.15"))
    recorder = _RecordingOperations(existing_volumes=(volume,))
    decision = prepare_storage(
        compose_config=_volume_config(), compose_state_dir=tmp_path, operations=recorder.build()
    )
    assert decision.action == "reset" and decision.stamped_line == "1.15"
    assert recorder.calls == [f"inspect:{volume}", "stop", f"remove:{volume}"]
    assert volume in recorder.messages[0]

    again = prepare_storage(
        compose_config=_volume_config(), compose_state_dir=tmp_path, operations=recorder.build()
    )
    assert again.action == "none"
    assert recorder.calls[-1] == f"inspect:{volume}"


def test_reset_intent_keeps_the_previous_owner_and_marks_reindex() -> None:
    legacy = reset_intent_stamp(None)
    assert legacy.release_line == UNKNOWN_RELEASE_LINE and legacy.reindex_pending
    previous = reset_intent_stamp(_stamp("1.15"))
    assert previous.release_line == "1.15" and previous.reindex_pending


@pytest.mark.parametrize("intent_line", ["1.15", UNKNOWN_RELEASE_LINE])
def test_reset_interrupted_after_clearing_still_reindexes(
    tmp_path: pathlib.Path, intent_line: str
) -> None:
    state_dir = tmp_path / "compose-state"
    storage_dir = state_dir / "qdrant"
    storage_dir.mkdir(parents=True)
    write_stamp(stamp_path(state_dir), _stamp(intent_line, pending=True))
    recorder = _RecordingOperations()

    decision = prepare_storage(
        compose_config=_bind_config(storage_dir),
        compose_state_dir=state_dir,
        operations=recorder.build(),
    )

    assert decision.action == "none"
    assert recorder.calls == []
    stamp = read_stamp(stamp_path(state_dir))
    assert stamp is not None and stamp.release_line == "1.20" and stamp.reindex_pending
    assert (
        complete_reindex(compose_state_dir=state_dir, qdrant_ready=True, reindex=lambda: True)
        == "reindexed"
    )


def test_old_stamp_without_pending_reindex_does_not_reindex_empty_storage(
    tmp_path: pathlib.Path,
) -> None:
    write_stamp(stamp_path(tmp_path), _stamp("1.15"))
    prepare_storage(
        compose_config=_bind_config(tmp_path / "qdrant"),
        compose_state_dir=tmp_path,
        operations=_RecordingOperations().build(),
    )
    stamp = read_stamp(stamp_path(tmp_path))
    assert stamp is not None and stamp.release_line == "1.20" and not stamp.reindex_pending


@pytest.mark.parametrize("volume_survives", [True, False])
def test_failed_volume_removal_is_recovered_with_a_reindex(
    tmp_path: pathlib.Path, volume_survives: bool
) -> None:
    volume = f"{_PROJECT}_qdrant-data"
    write_stamp(stamp_path(tmp_path), _stamp("1.15"))
    failing = _RecordingOperations(
        existing_volumes=(volume,), remove_error=QdrantStorageError("volume is in use")
    )
    with pytest.raises(QdrantStorageError, match="in use"):
        prepare_storage(
            compose_config=_volume_config(), compose_state_dir=tmp_path, operations=failing.build()
        )
    assert failing.calls == [f"inspect:{volume}", "stop", f"remove:{volume}"]
    intent = read_stamp(stamp_path(tmp_path))
    assert intent is not None and intent.release_line == "1.15" and intent.reindex_pending

    retry = _RecordingOperations(existing_volumes=(volume,) if volume_survives else ())
    decision = prepare_storage(
        compose_config=_volume_config(), compose_state_dir=tmp_path, operations=retry.build()
    )

    assert decision.action == ("reset" if volume_survives else "none")
    assert (f"remove:{volume}" in retry.calls) is volume_survives
    stamp = read_stamp(stamp_path(tmp_path))
    assert stamp is not None and stamp.release_line == "1.20" and stamp.reindex_pending
    assert (
        complete_reindex(compose_state_dir=tmp_path, qdrant_ready=True, reindex=lambda: True)
        == "reindexed"
    )


def test_volume_absent_is_stamped_without_reset(tmp_path: pathlib.Path) -> None:
    recorder = _RecordingOperations()
    decision = prepare_storage(
        compose_config=_volume_config(), compose_state_dir=tmp_path, operations=recorder.build()
    )
    assert decision.action == "none"
    assert recorder.calls == [f"inspect:{_PROJECT}_qdrant-data"]


def test_complete_reindex_outcomes(tmp_path: pathlib.Path) -> None:
    def must_not_run() -> bool:
        pytest.fail("reindex must only run when one is pending and qdrant is ready")

    assert complete_reindex(
        compose_state_dir=tmp_path, qdrant_ready=True, reindex=must_not_run
    ) == ("not-needed")
    write_stamp(stamp_path(tmp_path), _stamp("1.20", pending=True))
    assert complete_reindex(
        compose_state_dir=tmp_path, qdrant_ready=False, reindex=must_not_run
    ) == ("deferred")
    runs: list[str] = []

    def reindex() -> bool:
        runs.append("index")
        return True

    assert complete_reindex(compose_state_dir=tmp_path, qdrant_ready=True, reindex=reindex) == (
        "reindexed"
    )
    assert runs == ["index"]
    assert complete_reindex(compose_state_dir=tmp_path, qdrant_ready=True, reindex=reindex) == (
        "not-needed"
    )

    write_stamp(stamp_path(tmp_path), _stamp("1.20", pending=True))
    assert complete_reindex(
        compose_state_dir=tmp_path, qdrant_ready=True, reindex=lambda: False
    ) == ("skipped-no-hypermemory")
    stamp = read_stamp(stamp_path(tmp_path))
    assert stamp is not None and not stamp.reindex_pending


def test_ops_wraps_guard_failures_as_command_errors(
    tmp_path: pathlib.Path, test_context: TestContext
) -> None:
    state_dir = tmp_path / "compose-state"
    execution = strongclaw_ops._ComposeExecution(
        repo_root=tmp_path,
        compose_path=tmp_path / "docker-compose.yaml",
        cwd=tmp_path,
        env={"STRONGCLAW_COMPOSE_STATE_DIR": str(state_dir)},
    )

    def compose_config(_execution: object) -> dict[str, object]:
        return _bind_config(tmp_path / "not-qdrant")

    test_context.patch.patch_object(strongclaw_ops, "_compose_config_json", new=compose_config)
    with pytest.raises(CommandError, match="qdrant storage upgrade check failed: refusing"):
        strongclaw_ops._prepare_qdrant_storage(execution)
