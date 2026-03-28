"""Backup and recovery helpers for StrongClaw state."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import tarfile
import time

from clawops.strongclaw_runtime import (
    CommandError,
    resolve_home_dir,
    run_command,
)


def backups_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the backup archive directory."""
    return resolve_home_dir(home_dir) / ".openclaw" / "backups"


def openclaw_state_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the OpenClaw home directory."""
    return resolve_home_dir(home_dir) / ".openclaw"


def latest_backup_path(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the newest backup archive."""
    archive_candidates = sorted(backups_dir(home_dir=home_dir).glob("*.tar.gz"))
    if not archive_candidates:
        raise CommandError(f"no backup archives found in {backups_dir(home_dir=home_dir)}")
    return max(archive_candidates, key=lambda candidate: candidate.stat().st_mtime)


def create_backup(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Create one backup archive, preferring the OpenClaw CLI when available."""
    archive_root = backups_dir(home_dir=home_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    archive_path = archive_root / f"openclaw-{stamp}.tar.gz"
    if shutil.which("openclaw") is not None:
        result = run_command(
            ["openclaw", "backup", "create", str(archive_path)], timeout_seconds=600
        )
        if result.ok:
            return archive_path
    state_dir = openclaw_state_dir(home_dir=home_dir)
    archive_name = archive_path.name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in state_dir.rglob("*"):
            if archive_name in path.as_posix():
                continue
            archive.add(path, arcname=path.relative_to(resolve_home_dir(home_dir)))
    return archive_path


def verify_backup(
    target: pathlib.Path | str, *, home_dir: pathlib.Path | None = None
) -> pathlib.Path:
    """Verify one backup archive."""
    archive_path = (
        latest_backup_path(home_dir=home_dir)
        if str(target) == "latest"
        else pathlib.Path(target).expanduser().resolve()
    )
    if shutil.which("openclaw") is not None:
        result = run_command(
            ["openclaw", "backup", "verify", str(archive_path)], timeout_seconds=600
        )
        if not result.ok:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or "OpenClaw backup verification failed"
            )
            raise CommandError(detail)
        return archive_path
    with tarfile.open(archive_path, "r:gz") as archive:
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
        archive.extractall(destination)
    return destination


def prune_retention(
    *,
    home_dir: pathlib.Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Prune stale backup and log files."""
    now = time.time() if now_epoch is None else now_epoch
    retention_rules = (
        (backups_dir(home_dir=home_dir), 14 * 24 * 3600),
        (openclaw_state_dir(home_dir=home_dir) / "logs", 14 * 24 * 3600),
        (pathlib.Path("/tmp/openclaw"), 7 * 24 * 3600),
    )
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for recovery commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path, default=None)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("backup-create")
    verify_parser = subparsers.add_parser("backup-verify")
    verify_parser.add_argument("target", nargs="?", default="latest")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("archive")
    restore_parser.add_argument("destination", nargs="?", default=None)
    subparsers.add_parser("prune-retention")
    subparsers.add_parser("rotate-secrets")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for recovery commands."""
    args = parse_args(argv)
    home_dir = resolve_home_dir(args.home_dir)
    if args.command == "backup-create":
        payload = {"ok": True, "archive": str(create_backup(home_dir=home_dir))}
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
        payload = prune_retention(home_dir=home_dir)
    else:
        payload = rotation_guidance()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
