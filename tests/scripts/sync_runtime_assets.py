#!/usr/bin/env python3
"""Synchronize tracked platform assets into the packaged runtime mirror."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked_files(repo_root: Path, repo_relative_root: str) -> set[Path]:
    """Return tracked files under one repo-relative root."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", repo_relative_root],
        check=True,
        capture_output=True,
        text=False,
    )
    files: set[Path] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        files.add(Path(raw_path.decode("utf-8")))
    return files


def sync_runtime_assets(repo_root: Path) -> tuple[int, int]:
    """Copy tracked platform files into the packaged platform mirror."""
    resolved_root = repo_root.expanduser().resolve()
    source_root = resolved_root / "platform"
    target_root = resolved_root / "src" / "clawops" / "assets" / "platform"

    source_files = _tracked_files(resolved_root, "platform")
    target_files = _tracked_files(resolved_root, "src/clawops/assets/platform")

    copied = 0
    for source_path in sorted(source_files):
        relative = source_path.relative_to("platform")
        source_file = source_root / relative
        target_file = target_root / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if not target_file.exists() or source_file.read_bytes() != target_file.read_bytes():
            shutil.copy2(source_file, target_file)
            copied += 1

    removed = 0
    for target_path in sorted(target_files):
        relative = target_path.relative_to("src/clawops/assets/platform")
        if (Path("platform") / relative) in source_files:
            continue
        target_file = target_root / relative
        if target_file.exists():
            target_file.unlink()
            removed += 1

    return copied, removed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run runtime-asset synchronization."""
    args = _parse_args(argv)
    copied, removed = sync_runtime_assets(args.repo_root)
    print(f"synced runtime assets: copied={copied} removed={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
