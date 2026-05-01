"""Recovery create/plan orchestration."""

from __future__ import annotations

import pathlib
import tarfile
import time
from collections.abc import Callable

from clawops.recovery.backends import OpenClawBackupBackend, TarBackupBackend, WhichFunc
from clawops.recovery.models import BackupCreateExecution, RecoveryProfile
from clawops.recovery.planner import build_backup_plan
from clawops.strongclaw_runtime import CommandError, ExecResult

type RunCommandFunc = Callable[..., ExecResult]
type TarWriter = Callable[[pathlib.Path], None]
type SafeUnlink = Callable[[pathlib.Path], None]


def create_backup_execution(
    *,
    home_dir: pathlib.Path,
    openclaw_state_root: pathlib.Path,
    backup_root: pathlib.Path,
    legacy_backup_root: pathlib.Path,
    profile: RecoveryProfile,
    allow_fallback: bool,
    dry_run: bool,
    tar_writer: TarWriter,
    safe_unlink: SafeUnlink,
    which: WhichFunc,
    run_command: RunCommandFunc,
) -> BackupCreateExecution:
    """Plan or create one recovery backup archive."""
    plan = build_backup_plan(
        profile=profile,
        include_root=openclaw_state_root,
        backup_root=backup_root,
        legacy_backup_root=legacy_backup_root,
    )
    if dry_run:
        return BackupCreateExecution(plan=plan, dry_run=True)

    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    archive_path = backup_root / f"openclaw-{stamp}.tar.gz"
    archive_tmp_path = backup_root / f".{archive_path.name}.tmp"
    openclaw_backend = OpenClawBackupBackend(which=which, run_command=run_command)
    fallback_reason: str | None = None
    if openclaw_backend.is_available():
        safe_unlink(archive_tmp_path)
        backend_ok, backend_error = openclaw_backend.create(archive_tmp_path)
        if backend_ok:
            archive_tmp_path.replace(archive_path)
            return BackupCreateExecution(
                plan=plan,
                dry_run=False,
                archive_path=archive_path,
                mode="openclaw-cli",
            )
        safe_unlink(archive_tmp_path)
        if not allow_fallback:
            raise CommandError(backend_error or "openclaw backup create failed")
        fallback_reason = backend_error

    safe_unlink(archive_tmp_path)
    fallback_backend = TarBackupBackend(writer=tar_writer)
    try:
        fallback_backend.create(archive_tmp_path)
        archive_tmp_path.replace(archive_path)
    except (OSError, tarfile.TarError) as exc:
        safe_unlink(archive_tmp_path)
        safe_unlink(archive_path)
        raise CommandError(f"backup creation failed: {exc}") from exc
    return BackupCreateExecution(
        plan=plan,
        dry_run=False,
        archive_path=archive_path,
        mode="tar-fallback",
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
    )
