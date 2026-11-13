"""Recovery features: OpenClaw-backed backups, verification, restore, retention, scheduled jobs."""

from __future__ import annotations

import json
import os
import tarfile
import time
from pathlib import Path
from typing import cast

from clawops.strongclaw_recovery import backups_dir
from tests.utils.helpers.feature_verification import CommandOutcome, FeatureHost

SCHEDULED_JOB_TIMEOUT_SECONDS = 600.0
_POLL_SECONDS = 3.0


def _recovery(feature_host: FeatureHost, *arguments: str, timeout: float = 900) -> CommandOutcome:
    return feature_host.clawops(
        "recovery", "--home-dir", str(feature_host.home), *arguments, timeout=timeout
    )


def _create_backup(feature_host: FeatureHost, *, allow_fallback: bool = True) -> dict[str, object]:
    """Create an archive; dependent features accept the operator tar fallback so they verify
    their own behaviour even when the OpenClaw-backed path is broken (recovery.backup-create)."""
    arguments = ("backup-create", "--allow-fallback") if allow_fallback else ("backup-create",)
    return cast(dict[str, object], _recovery(feature_host, *arguments).require_ok().json())


def test_backup_create_uses_the_openclaw_backend(feature_host: FeatureHost) -> None:
    payload = _create_backup(feature_host, allow_fallback=False)
    assert payload["mode"] == "openclaw-cli", f"backup fell back to tar: {payload}"
    assert payload["fallback_used"] is False, payload
    archive = Path(str(payload["archive"]))
    assert archive.is_file() and archive.stat().st_size > 0, payload
    assert archive.parent == backups_dir(home_dir=feature_host.home), archive


def test_backup_verify_accepts_latest_archive(feature_host: FeatureHost) -> None:
    created = _create_backup(feature_host)
    verified = cast(dict[str, object], _recovery(feature_host, "backup-verify").require_ok().json())
    assert verified["ok"] is True and verified["archive"] == created["archive"], verified


def _expected_restored_paths(archive: Path, *, home: Path) -> set[str]:
    """Return the home-relative files a restore must produce from *archive*.

    A tar-fallback archive already stores home-relative paths. An `openclaw backup create`
    archive stores each manifest asset under `<root>/payload/posix/<absolute path>`, and a
    restore lays those out relative to the backed-up home.
    """
    with tarfile.open(archive, "r:gz") as bundle:
        files = [member.name for member in bundle.getmembers() if member.isfile()]
        manifests = [name for name in files if name.count("/") == 1]
        manifest_name = next((name for name in manifests if name.endswith("/manifest.json")), None)
        if manifest_name is None:
            return set(files)
        handle = bundle.extractfile(manifest_name)
        assert handle is not None
        manifest = cast(dict[str, object], json.load(handle))
    resolved_home = home.resolve()
    prefixes = [
        (
            str(cast(dict[str, object], asset)["archivePath"]),
            Path(str(cast(dict[str, object], asset)["sourcePath"]))
            .resolve()
            .relative_to(resolved_home)
            .as_posix(),
        )
        for asset in cast(list[object], manifest["assets"])
    ]
    expected: set[str] = set()
    for name in files:
        for prefix, relative in prefixes:
            if name.startswith(f"{prefix}/"):
                expected.add(f"{relative}/{name[len(prefix) + 1 :]}")
                break
            if name == prefix:
                expected.add(relative)
                break
    return expected


def test_restore_extracts_openclaw_state(feature_host: FeatureHost) -> None:
    archive = Path(str(_create_backup(feature_host)["archive"]))
    destination = feature_host.scratch("restore")
    restored = cast(
        dict[str, object],
        _recovery(feature_host, "restore", str(archive), str(destination)).require_ok().json(),
    )
    assert Path(str(restored["destination"])) == destination, restored
    member_names = _expected_restored_paths(archive, home=feature_host.home)
    assert member_names, "the archive holds no files"
    restored_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert member_names <= restored_files, sorted(member_names - restored_files)[:20]
    assert any(name.endswith("openclaw.json") for name in restored_files), sorted(restored_files)[
        :40
    ]


def test_prune_retention_removes_only_stale_archives(feature_host: FeatureHost) -> None:
    archive_root = backups_dir(home_dir=feature_host.home)
    archive_root.mkdir(parents=True, exist_ok=True)
    stale = archive_root / "featver-stale.tar.gz"
    stale.write_bytes(b"stale")
    old = time.time() - 30 * 24 * 3600
    os.utime(stale, (old, old))
    fresh = Path(str(_create_backup(feature_host)["archive"]))
    payload = cast(
        dict[str, object], _recovery(feature_host, "prune-retention").require_ok().json()
    )
    assert stale.as_posix() in cast(list[str], payload["deleted"]), payload
    assert not stale.exists() and fresh.is_file(), payload


def test_rotate_secrets_guidance_lists_rotation_targets(feature_host: FeatureHost) -> None:
    payload = cast(dict[str, object], _recovery(feature_host, "rotate-secrets").require_ok().json())
    steps = cast(list[str], payload["steps"])
    assert payload["ok"] is True and steps, payload
    assert any("Restart the gateway" in step for step in steps), steps


def test_scheduled_backup_job_produces_an_archive(feature_host: FeatureHost) -> None:
    archive_root = backups_dir(home_dir=feature_host.home)
    before: set[Path] = set(archive_root.glob("*")) if archive_root.is_dir() else set()
    if feature_host.lane.platform == "linux":
        timer = feature_host.run(
            ["systemctl", "--user", "is-active", "openclaw-backup-create.timer"], timeout=30
        )
        assert timer.stdout.strip() == "active", timer.describe()
        feature_host.run(
            ["systemctl", "--user", "start", "openclaw-backup-create.service"],
            timeout=SCHEDULED_JOB_TIMEOUT_SECONDS,
        ).require_ok()
        result = feature_host.run(
            ["systemctl", "--user", "show", "--property=Result", "openclaw-backup-create.service"],
            timeout=30,
        ).require_ok()
        assert "Result=success" in result.stdout, result.describe()
    else:
        label = f"gui/{os.getuid()}/ai.openclaw.backup-create"
        feature_host.run(["launchctl", "kickstart", "-k", label], timeout=60).require_ok()
        deadline = time.monotonic() + SCHEDULED_JOB_TIMEOUT_SECONDS
        state = feature_host.run(["launchctl", "print", label], timeout=30)
        while "last exit code = 0" not in state.stdout and time.monotonic() < deadline:
            assert "last exit code = (never exited)" in state.stdout or "state = running" in (
                state.stdout
            ), state.describe()
            time.sleep(_POLL_SECONDS)
            state = feature_host.run(["launchctl", "print", label], timeout=30)
        assert "last exit code = 0" in state.stdout, state.describe()
    created = set(archive_root.glob("*")) - before
    assert created, f"the scheduled backup job wrote no archive under {archive_root}"
