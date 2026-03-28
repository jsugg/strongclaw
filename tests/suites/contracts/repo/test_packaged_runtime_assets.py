"""Contract tests for the packaged StrongClaw runtime asset mirror."""

from __future__ import annotations

import pathlib
import subprocess

from tests.utils.helpers.repo import REPO_ROOT


def _tracked_relative_files(root: pathlib.Path) -> set[pathlib.Path]:
    """Return existing git-tracked files under one repo-relative root."""
    repo_relative_root = root.resolve().relative_to(REPO_ROOT)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "-z",
            repo_relative_root.as_posix(),
        ],
        check=True,
        capture_output=True,
        text=False,
    )
    files: set[pathlib.Path] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        tracked_path = pathlib.Path(raw_path.decode("utf-8"))
        relative_path = tracked_path.relative_to(repo_relative_root)
        if (root / relative_path).is_file():
            files.add(relative_path)
    return files


def test_packaged_platform_asset_tree_matches_source_tree() -> None:
    source_root = REPO_ROOT / "platform"
    packaged_root = REPO_ROOT / "src" / "clawops" / "assets" / "platform"

    source_files = _tracked_relative_files(source_root)
    packaged_files = _tracked_relative_files(packaged_root)

    assert packaged_files == source_files
    for relative_path in sorted(source_files):
        assert (packaged_root / relative_path).read_bytes() == (
            source_root / relative_path
        ).read_bytes()
