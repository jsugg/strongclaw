"""Deterministic recovery backup planning."""

from __future__ import annotations

import pathlib

from clawops.recovery.models import BackupPlan, RecoveryProfile
from clawops.recovery.policy import retention_for_profile


def _is_path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether *path* is contained by *root*."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _estimate_plan(
    include_root: pathlib.Path, exclude_roots: tuple[pathlib.Path, ...]
) -> tuple[int, int]:
    """Estimate bytes and file count for the plan."""
    file_count = 0
    total_bytes = 0
    for path in sorted(include_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if any(_is_path_within(resolved, root) for root in exclude_roots):
            continue
        file_count += 1
        total_bytes += path.stat().st_size
    return file_count, total_bytes


def build_backup_plan(
    *,
    profile: RecoveryProfile,
    include_root: pathlib.Path,
    backup_root: pathlib.Path,
    legacy_backup_root: pathlib.Path,
) -> BackupPlan:
    """Build one deterministic backup plan."""
    include_root_resolved = include_root.resolve()
    backup_root_resolved = backup_root.resolve()
    legacy_backup_root_resolved = legacy_backup_root.resolve()
    exclude_roots = (backup_root_resolved, legacy_backup_root_resolved)
    estimated_file_count, estimated_bytes = _estimate_plan(include_root_resolved, exclude_roots)
    return BackupPlan(
        profile=profile,
        include_roots=(include_root_resolved,),
        exclude_roots=exclude_roots,
        backend_candidates=("openclaw-cli", "tar-fallback"),
        estimated_bytes=estimated_bytes,
        estimated_file_count=estimated_file_count,
        retention=retention_for_profile(profile),
    )
