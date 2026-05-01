"""Backup and recovery helpers for StrongClaw state."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import tarfile
import time

from clawops.app_paths import strongclaw_state_dir
from clawops.cli_roots import add_ignored_repo_root_alias, warn_ignored_repo_root_argument
from clawops.recovery.models import BackupCreateExecution
from clawops.recovery.orchestrator import create_backup_execution
from clawops.recovery.policy import (
    DEFAULT_RECOVERY_PROFILE,
    RECOVERY_PROFILES,
    ensure_recovery_profile,
)
from clawops.strongclaw_runtime import (
    CommandError,
    resolve_home_dir,
    run_command,
)

_OPENCLAW_VERIFY_MANIFEST_MISMATCH = "Expected exactly one backup manifest entry"


def backups_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the StrongClaw-managed backup archive directory."""
    resolved_home = resolve_home_dir(home_dir)
    return strongclaw_state_dir(home_dir=resolved_home) / "backups"


def openclaw_state_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the OpenClaw home directory."""
    return resolve_home_dir(home_dir) / ".openclaw"


def legacy_backups_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the legacy OpenClaw-local backup archive directory."""
    return openclaw_state_dir(home_dir=home_dir) / "backups"


def latest_backup_path(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the newest backup archive."""
    archive_candidates = sorted(backups_dir(home_dir=home_dir).glob("*.tar.gz"))
    if not archive_candidates:
        raise CommandError(f"no backup archives found in {backups_dir(home_dir=home_dir)}")
    return max(archive_candidates, key=lambda candidate: candidate.stat().st_mtime)


def _is_path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether *path* is contained by *root* after resolution."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_unlink(path: pathlib.Path) -> None:
    """Remove one file when present."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _write_tar_archive(
    archive_path: pathlib.Path,
    *,
    state_dir: pathlib.Path,
    include_root: pathlib.Path,
    exclude_roots: tuple[pathlib.Path, ...],
) -> None:
    """Write a fallback tar archive with explicit path exclusions."""
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in state_dir.rglob("*"):
            if path.is_symlink():
                continue
            if not (path.is_file() or path.is_dir()):
                continue
            resolved_path = path.resolve()
            if any(_is_path_within(resolved_path, root) for root in exclude_roots):
                continue
            archive.add(path, arcname=path.relative_to(include_root), recursive=False)


def _create_backup_result(
    *,
    home_dir: pathlib.Path | None = None,
    profile: str = DEFAULT_RECOVERY_PROFILE,
    allow_fallback: bool = False,
    dry_run: bool = False,
) -> BackupCreateExecution:
    """Create one backup archive and record which backup path was used."""
    resolved_home_dir = resolve_home_dir(home_dir)
    archive_root = backups_dir(home_dir=home_dir)
    selected_profile = ensure_recovery_profile(profile)
    exclude_roots: tuple[pathlib.Path, ...] = (
        archive_root.resolve(),
        legacy_backups_dir(home_dir=home_dir).resolve(),
    )
    state_dir = openclaw_state_dir(home_dir=home_dir)
    return create_backup_execution(
        home_dir=resolved_home_dir,
        openclaw_state_root=state_dir,
        backup_root=archive_root,
        legacy_backup_root=legacy_backups_dir(home_dir=home_dir),
        profile=selected_profile,
        allow_fallback=allow_fallback,
        dry_run=dry_run,
        tar_writer=lambda archive_tmp_path: _write_tar_archive(
            archive_tmp_path,
            state_dir=state_dir,
            include_root=resolved_home_dir,
            exclude_roots=exclude_roots,
        ),
        safe_unlink=_safe_unlink,
        which=shutil.which,
        run_command=run_command,
    )


def create_backup(
    *,
    home_dir: pathlib.Path | None = None,
    profile: str = DEFAULT_RECOVERY_PROFILE,
    allow_fallback: bool = False,
) -> pathlib.Path:
    """Create one backup archive, preferring the OpenClaw CLI when available."""
    result = _create_backup_result(
        home_dir=home_dir,
        profile=profile,
        allow_fallback=allow_fallback,
        dry_run=False,
    )
    if result.archive_path is None:
        raise CommandError("backup creation produced no archive path")
    return result.archive_path


def verify_backup(
    target: pathlib.Path | str, *, home_dir: pathlib.Path | None = None
) -> pathlib.Path:
    """Verify one backup archive."""
    archive_path = (
        latest_backup_path(home_dir=home_dir)
        if str(target) == "latest"
        else pathlib.Path(target).expanduser().resolve()
    )
    openclaw_verify_result = None
    if shutil.which("openclaw") is not None:
        openclaw_verify_result = run_command(
            ["openclaw", "backup", "verify", str(archive_path)], timeout_seconds=600
        )
        if openclaw_verify_result.ok:
            return archive_path
        detail = (
            openclaw_verify_result.stderr.strip()
            or openclaw_verify_result.stdout.strip()
            or "OpenClaw backup verification failed"
        )
        if _OPENCLAW_VERIFY_MANIFEST_MISMATCH in detail:
            try:
                return _verify_tar_archive(archive_path)
            except (OSError, tarfile.TarError):
                pass
        raise CommandError(detail)
    return _verify_tar_archive(archive_path)


def _verify_tar_archive(archive_path: pathlib.Path) -> pathlib.Path:
    """Verify one fallback tar archive by ensuring members can be enumerated."""
    with tarfile.open(archive_path, "r:gz") as archive:
        # Simply ensure the archive can be opened and its members enumerated.
        archive.getmembers()
    return archive_path


def restore_backup(
    archive_path: pathlib.Path,
    *,
    destination: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Restore one backup archive into a destination directory."""
    verified_path = verify_backup(archive_path, home_dir=home_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(verified_path, "r:gz") as archive:
        safe_members = _validated_archive_members(archive, destination=destination)
        archive.extractall(destination, members=safe_members, filter="data")
    return destination


def prune_retention(
    *,
    home_dir: pathlib.Path | None = None,
    now_epoch: float | None = None,
    include_shared_tmp: bool = False,
) -> dict[str, object]:
    """Prune stale backup and log files."""
    now = time.time() if now_epoch is None else now_epoch
    retention_rules: list[tuple[pathlib.Path, int]] = [
        (backups_dir(home_dir=home_dir), 14 * 24 * 3600),
        (openclaw_state_dir(home_dir=home_dir) / "logs", 14 * 24 * 3600),
    ]
    if include_shared_tmp:
        retention_rules.append((pathlib.Path("/tmp/openclaw"), 7 * 24 * 3600))
    deleted: list[str] = []
    for root, max_age_seconds in retention_rules:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if now - path.stat().st_mtime <= max_age_seconds:
                continue
            path.unlink()
            deleted.append(path.as_posix())
    return {"ok": True, "deleted": deleted}


def rotation_guidance() -> dict[str, object]:
    """Return the manual secret-rotation guidance."""
    return {
        "ok": True,
        "steps": [
            "Rotate secrets in the source-of-truth secret store first.",
            "Update the StrongClaw env contract or Varlock plugin mapping.",
            "Run `varlock load --path platform/configs/varlock` to validate the refreshed secrets.",
            "Restart the gateway and sidecars after the new secrets are in place.",
        ],
    }


def _validated_archive_members(
    archive: tarfile.TarFile, *, destination: pathlib.Path
) -> list[tarfile.TarInfo]:
    """Reject unsafe archive members before extraction."""
    destination_root = destination.resolve()
    safe_members: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        member_path = pathlib.PurePosixPath(member.name)
        if member_path.is_absolute():
            raise CommandError(f"unsafe backup archive member uses an absolute path: {member.name}")
        if any(part == ".." for part in member_path.parts):
            raise CommandError(
                f"unsafe backup archive member escapes the restore root: {member.name}"
            )
        if member.issym() or member.islnk():
            raise CommandError(f"unsafe backup archive member is a link: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise CommandError(f"unsupported backup archive member type for restore: {member.name}")
        target_path = (destination_root / pathlib.Path(*member_path.parts)).resolve()
        if not target_path.is_relative_to(destination_root):
            raise CommandError(
                f"unsafe backup archive member escapes the restore destination: {member.name}"
            )
        safe_members.append(member)
    return safe_members


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for recovery commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_ignored_repo_root_alias(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_create_parser = subparsers.add_parser("backup-create")
    backup_create_parser.add_argument("--profile", choices=RECOVERY_PROFILES, default=None)
    backup_create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the deterministic backup plan without writing archives.",
    )
    backup_create_parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Allow fallback tar creation when OpenClaw CLI backup creation fails.",
    )
    verify_parser = subparsers.add_parser("backup-verify")
    verify_parser.add_argument("target", nargs="?", default="latest")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("archive")
    restore_parser.add_argument("destination", nargs="?", default=None)
    prune_parser = subparsers.add_parser("prune-retention")
    prune_parser.add_argument(
        "--include-shared-tmp",
        action="store_true",
        help="Also prune /tmp/openclaw when the operator explicitly owns that state.",
    )
    subparsers.add_parser("rotate-secrets")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for recovery commands."""
    args = parse_args(argv)
    warn_ignored_repo_root_argument(
        args,
        command_name="clawops recovery",
        guidance="use --home-dir to target an alternate OpenClaw home.",
    )
    home_dir = resolve_home_dir(args.home_dir)
    if args.command == "backup-create":
        execution = _create_backup_result(
            home_dir=home_dir,
            profile=args.profile or DEFAULT_RECOVERY_PROFILE,
            allow_fallback=bool(args.allow_fallback),
            dry_run=bool(args.dry_run),
        )
        payload: dict[str, object] = {
            "ok": True,
            **execution.plan.to_payload(),
            "dry_run": execution.dry_run,
        }
        if execution.archive_path is not None and execution.mode is not None:
            payload["archive"] = str(execution.archive_path)
            payload["mode"] = execution.mode
            payload["fallback_used"] = execution.fallback_used
            if execution.fallback_reason is not None:
                payload["fallback_reason"] = execution.fallback_reason
    elif args.command == "backup-verify":
        payload = {"ok": True, "archive": str(verify_backup(args.target, home_dir=home_dir))}
    elif args.command == "restore":
        destination = (
            pathlib.Path(args.destination).expanduser().resolve()
            if args.destination is not None
            else home_dir.parent / ".openclaw-restore"
        )
        payload = {
            "ok": True,
            "destination": str(
                restore_backup(
                    pathlib.Path(args.archive).expanduser().resolve(),
                    destination=destination,
                    home_dir=home_dir,
                )
            ),
        }
    elif args.command == "prune-retention":
        payload = prune_retention(
            home_dir=home_dir,
            include_shared_tmp=bool(args.include_shared_tmp),
        )
    else:
        payload = rotation_guidance()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
