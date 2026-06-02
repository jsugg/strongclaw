"""Unit coverage for StrongClaw recovery helpers."""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from typing import cast

import pytest

from clawops import strongclaw_recovery
from clawops.app_paths import strongclaw_state_dir
from clawops.strongclaw_runtime import CommandError, ExecResult
from tests.plugins.infrastructure.context import TestContext

FALLBACK_MANIFEST_PATH = ".strongclaw/backup-manifest.json"


def _init_openclaw_home(home_dir: Path) -> Path:
    """Create a minimal OpenClaw home tree for recovery tests."""
    state_dir = home_dir / ".openclaw"
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    (state_dir / "config.json").write_text('{"ok": true}\n', encoding="utf-8")
    (state_dir / "logs" / "gateway.log").write_text("ready\n", encoding="utf-8")
    return state_dir


def _write_payload_member(archive_path: Path, member_name: str) -> None:
    """Write one tar archive containing a single regular-file member."""
    payload = b"unsafe\n"
    manifest_payload = json.dumps(
        {
            "profile": "control-plane",
            "include_roots": ["/tmp/home"],
            "exclude_roots": [
                "/tmp/home/.local/state/strongclaw/backups",
                "/tmp/home/.openclaw/backups",
            ],
            "file_count": 1,
            "bytes": len(payload),
            "content_sha256": "dummy",
            "backend": "tar-fallback",
        },
        sort_keys=True,
    ).encode("utf-8")
    member = tarfile.TarInfo(member_name)
    member.size = len(payload)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.addfile(member, io.BytesIO(payload))
        manifest_member = tarfile.TarInfo(FALLBACK_MANIFEST_PATH)
        manifest_member.size = len(manifest_payload)
        archive.addfile(manifest_member, io.BytesIO(manifest_payload))


def _missing_tool(_command: str, _path: str | None = None) -> str | None:
    """Return a typed `shutil.which` stub that forces fallback paths."""
    return None


def _openclaw_only(command: str, _path: str | None = None) -> str | None:
    """Return a typed `shutil.which` stub that only resolves openclaw."""
    if command == "openclaw":
        return "/usr/bin/openclaw"
    return None


def test_backup_create_cli_reports_tar_fallback_and_round_trips(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    """Backup creation should surface fallback mode and restore the archived state."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)

    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_missing_tool,
    )

    exit_code = strongclaw_recovery.main(["--home-dir", str(home_dir), "backup-create"])
    payload = json.loads(capsys.readouterr().out)
    archive_path = Path(payload["archive"])

    assert exit_code == 0
    assert payload["mode"] == "tar-fallback"
    assert payload["manifest"]["backend"] == "tar-fallback"
    assert payload["manifest"]["profile"] == "control-plane"
    assert payload["manifest"]["file_count"] >= 1
    assert payload["manifest"]["bytes"] >= 1
    assert payload["manifest"]["content_sha256"]
    assert archive_path.is_file()
    assert strongclaw_recovery.verify_backup("latest", home_dir=home_dir) == archive_path

    restored = tmp_path / "restored"
    strongclaw_recovery.restore_backup(archive_path, destination=restored, home_dir=home_dir)

    assert (restored / ".openclaw" / "config.json").read_text(encoding="utf-8") == '{"ok": true}\n'
    assert (restored / ".openclaw" / "logs" / "gateway.log").read_text(
        encoding="utf-8"
    ) == "ready\n"


def test_backup_create_cli_dry_run_outputs_plan_without_writing_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    """Dry-run backup creation should emit the manifest without writing archives."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)
    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)

    exit_code = strongclaw_recovery.main(
        [
            "--home-dir",
            str(home_dir),
            "backup-create",
            "--profile",
            "control-plane",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["profile"] == "control-plane"
    assert payload["backend_candidates"] == ["openclaw-cli", "tar-fallback"]
    assert payload["include_roots"] == [str(home_dir / ".openclaw")]
    assert payload["exclude_roots"] == [
        str(strongclaw_recovery.backups_dir(home_dir=home_dir)),
        str(strongclaw_recovery.legacy_backups_dir(home_dir=home_dir)),
    ]
    backup_root = strongclaw_recovery.backups_dir(home_dir=home_dir)
    assert not backup_root.exists()


def test_backup_root_defaults_to_strongclaw_state_dir(tmp_path: Path) -> None:
    """Backups should default to the StrongClaw-owned state tree."""
    home_dir = tmp_path / "home"
    expected_root = strongclaw_state_dir(home_dir=home_dir) / "backups"
    backup_root = strongclaw_recovery.backups_dir(home_dir=home_dir)

    assert backup_root == expected_root
    assert not backup_root.is_relative_to(strongclaw_recovery.openclaw_state_dir(home_dir=home_dir))


def test_backup_create_fallback_excludes_legacy_openclaw_backup_root(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Fallback tar backups should never include legacy `~/.openclaw/backups` entries."""
    home_dir = tmp_path / "home"
    state_dir = _init_openclaw_home(home_dir)
    legacy_archive = state_dir / "backups" / "old.tar.gz"
    legacy_archive.parent.mkdir(parents=True, exist_ok=True)
    legacy_archive.write_text("legacy\n", encoding="utf-8")
    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)

    archive_path = strongclaw_recovery.create_backup(home_dir=home_dir)
    with tarfile.open(archive_path, "r:gz") as archive:
        member_names = [member.name for member in archive.getmembers()]

    assert all(not name.startswith(".openclaw/backups") for name in member_names)


def test_backup_create_failure_cleans_partial_archive(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Backup creation failures should remove temporary and partial archive files."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)
    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)

    def _failing_write_tar_archive(*args: object, **kwargs: object) -> None:
        archive_path = cast(Path, args[0])
        archive_path.write_bytes(b"partial")
        raise OSError("no space left on device")

    test_context.patch.patch_object(
        strongclaw_recovery,
        "_write_tar_archive",
        new=_failing_write_tar_archive,
    )

    with pytest.raises(CommandError, match="backup creation failed"):
        strongclaw_recovery.create_backup(home_dir=home_dir)

    backup_root = strongclaw_recovery.backups_dir(home_dir=home_dir)
    remaining_files = list(backup_root.iterdir()) if backup_root.exists() else []
    assert remaining_files == []


def test_backup_create_fails_closed_without_allow_fallback_when_openclaw_create_fails(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Create should fail closed when the OpenClaw backend fails and fallback is not allowed."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)

    def _run_command(_command: list[str], **_kwargs: object) -> ExecResult:
        return ExecResult(
            argv=("openclaw", "backup", "create", "target"),
            returncode=1,
            stdout="",
            stderr="openclaw backend failed",
            duration_ms=1,
        )

    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_openclaw_only)
    test_context.patch.patch_object(strongclaw_recovery, "run_command", new=_run_command)

    with pytest.raises(CommandError, match="openclaw backend failed"):
        strongclaw_recovery.create_backup(home_dir=home_dir, allow_fallback=False)


def test_backup_create_allows_explicit_fallback_when_openclaw_create_fails(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Create should use tar fallback only when the operator opts into fallback mode."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)

    def _run_command(_command: list[str], **_kwargs: object) -> ExecResult:
        return ExecResult(
            argv=("openclaw", "backup", "create", "target"),
            returncode=1,
            stdout="",
            stderr="openclaw backend failed",
            duration_ms=1,
        )

    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_openclaw_only)
    test_context.patch.patch_object(strongclaw_recovery, "run_command", new=_run_command)

    archive_path = strongclaw_recovery.create_backup(home_dir=home_dir, allow_fallback=True)
    assert archive_path.is_file()
    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)
    assert strongclaw_recovery.verify_backup(archive_path, home_dir=home_dir) == archive_path


def test_restore_backup_rejects_traversal_members(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Restore should fail closed when the archive attempts path traversal."""
    archive_path = tmp_path / "traversal.tar.gz"
    _write_payload_member(archive_path, "../../escape.txt")
    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_missing_tool,
    )

    with pytest.raises(CommandError, match="escapes the restore root"):
        strongclaw_recovery.restore_backup(
            archive_path,
            destination=tmp_path / "restore",
            home_dir=tmp_path / "home",
        )


def test_restore_backup_rejects_link_members(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Restore should reject symlink and hardlink archive members."""
    archive_path = tmp_path / "link.tar.gz"
    link_member = tarfile.TarInfo("state-link")
    link_member.type = tarfile.SYMTYPE
    link_member.linkname = "target"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.addfile(link_member)
        manifest_payload = json.dumps(
            {
                "profile": "control-plane",
                "include_roots": ["/tmp/home"],
                "exclude_roots": [
                    "/tmp/home/.local/state/strongclaw/backups",
                    "/tmp/home/.openclaw/backups",
                ],
                "file_count": 0,
                "bytes": 0,
                "content_sha256": "dummy",
                "backend": "tar-fallback",
            },
            sort_keys=True,
        ).encode("utf-8")
        manifest_member = tarfile.TarInfo(FALLBACK_MANIFEST_PATH)
        manifest_member.size = len(manifest_payload)
        archive.addfile(manifest_member, io.BytesIO(manifest_payload))
    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_missing_tool,
    )

    with pytest.raises(CommandError, match="is a link"):
        strongclaw_recovery.restore_backup(
            archive_path,
            destination=tmp_path / "restore",
            home_dir=tmp_path / "home",
        )


def test_verify_backup_falls_back_when_openclaw_verify_rejects_tar_archive(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Verify should fall back to tar validation on OpenClaw manifest-compat failures."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)
    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_missing_tool,
    )
    archive_path = strongclaw_recovery.create_backup(home_dir=home_dir)

    def _with_openclaw(_command: str, _path: str | None = None) -> str | None:
        return "/usr/bin/openclaw"

    def _failed_openclaw_verify(*_args: object, **_kwargs: object) -> ExecResult:
        return ExecResult(
            argv=("openclaw", "backup", "verify", str(archive_path)),
            returncode=1,
            stdout="",
            stderr="Error: Expected exactly one backup manifest entry, found 0.",
            duration_ms=1,
        )

    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_with_openclaw,
    )
    test_context.patch.patch_object(
        strongclaw_recovery,
        "run_command",
        new=_failed_openclaw_verify,
    )

    assert strongclaw_recovery.verify_backup(archive_path, home_dir=home_dir) == archive_path


def test_prune_retention_deletes_only_expired_strongclaw_owned_files(tmp_path: Path) -> None:
    """Retention pruning should stay scoped to StrongClaw-owned backup and log roots."""
    home_dir = tmp_path / "home"
    backups_dir = strongclaw_recovery.backups_dir(home_dir=home_dir)
    logs_dir = strongclaw_recovery.openclaw_state_dir(home_dir=home_dir) / "logs"
    backups_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    old_backup = backups_dir / "old.tar.gz"
    recent_backup = backups_dir / "recent.tar.gz"
    old_log = logs_dir / "old.log"
    recent_log = logs_dir / "recent.log"
    external_file = tmp_path / "shared.log"

    for path in (old_backup, recent_backup, old_log, recent_log, external_file):
        path.write_text(path.name, encoding="utf-8")

    now_epoch = 2_000_000_000.0
    old_epoch = now_epoch - (15 * 24 * 3600)
    recent_epoch = now_epoch - (2 * 24 * 3600)
    for path in (old_backup, old_log):
        os.utime(path, (old_epoch, old_epoch))
    for path in (recent_backup, recent_log, external_file):
        os.utime(path, (recent_epoch, recent_epoch))

    payload = strongclaw_recovery.prune_retention(home_dir=home_dir, now_epoch=now_epoch)
    deleted = cast(list[str], payload["deleted"])

    assert payload["ok"] is True
    assert sorted(deleted) == sorted([old_backup.as_posix(), old_log.as_posix()])
    assert not old_backup.exists()
    assert not old_log.exists()
    assert recent_backup.exists()
    assert recent_log.exists()
    assert external_file.exists()


def test_verify_backup_falls_back_when_openclaw_manifest_is_missing(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Fallback tar archives should still verify when openclaw manifest checks fail."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)

    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_missing_tool,
    )
    archive_path = strongclaw_recovery.create_backup(home_dir=home_dir)

    def _run_command(command: list[str], **_kwargs: object) -> ExecResult:
        assert command[:3] == ["openclaw", "backup", "verify"]
        return ExecResult(
            argv=tuple(command),
            returncode=1,
            stdout="",
            stderr="Error: Expected exactly one backup manifest entry, found 0.",
            duration_ms=1,
        )

    test_context.patch.patch_object(
        strongclaw_recovery.shutil,
        "which",
        new=_openclaw_only,
    )
    test_context.patch.patch_object(
        strongclaw_recovery,
        "run_command",
        new=_run_command,
    )

    assert strongclaw_recovery.verify_backup(archive_path, home_dir=home_dir) == archive_path


def test_verify_backup_fails_when_fallback_manifest_is_missing(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Fallback archive verification should fail when the embedded manifest is absent."""
    archive_path = tmp_path / "missing-manifest.tar.gz"
    payload = b"data\n"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo(name=".openclaw/config.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)
    with pytest.raises(CommandError, match="invalid fallback backup manifest count"):
        strongclaw_recovery.verify_backup(archive_path, home_dir=tmp_path / "home")


def test_recovery_emits_structured_events_for_backup_and_prune(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    """Recovery CLI should emit structured events with required fields when enabled."""
    home_dir = tmp_path / "home"
    _init_openclaw_home(home_dir)
    test_context.patch.patch_object(strongclaw_recovery.shutil, "which", new=_missing_tool)
    previous_structured_logs = os.environ.get("CLAWOPS_STRUCTURED_LOGS")
    try:
        os.environ["CLAWOPS_STRUCTURED_LOGS"] = "1"
        strongclaw_recovery.main(["--home-dir", str(home_dir), "backup-create"])
        strongclaw_recovery.main(["--home-dir", str(home_dir), "backup-verify", "latest"])
        strongclaw_recovery.main(["--home-dir", str(home_dir), "prune-retention"])
    finally:
        if previous_structured_logs is None:
            os.environ.pop("CLAWOPS_STRUCTURED_LOGS", None)
        else:
            os.environ["CLAWOPS_STRUCTURED_LOGS"] = previous_structured_logs

    stderr_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    events = [json.loads(line) for line in stderr_lines]
    by_event: dict[str, dict[str, object]] = {}
    for event in events:
        event_name = event.get("event")
        if isinstance(event_name, str):
            by_event[event_name] = event

    plan_event = by_event["clawops.recovery.backup_plan_built"]
    create_event = by_event["clawops.recovery.backup_create_completed"]
    verify_event = by_event["clawops.recovery.backup_verify_completed"]
    prune_event = by_event["clawops.recovery.backup_prune_completed"]

    assert plan_event["profile"] == "control-plane"
    assert plan_event["excluded_paths_count"] == 2
    assert create_event["backend"] == "tar-fallback"
    assert create_event["result"] == "ok"
    assert isinstance(create_event["file_count"], int)
    assert create_event["file_count"] >= 1
    assert isinstance(create_event["bytes"], int)
    assert create_event["bytes"] >= 1
    assert verify_event["result"] == "ok"
    assert prune_event["result"] == "ok"
