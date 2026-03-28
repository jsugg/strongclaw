"""Contract tests for the packaged StrongClaw runtime asset mirror."""

from __future__ import annotations

import pathlib

from tests.utils.helpers.repo import REPO_ROOT


def _relative_files(root: pathlib.Path) -> set[pathlib.Path]:
    """Return the normalized file set under one root."""
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and "__pycache__" not in path.parts
        and "node_modules" not in path.parts
    }


def test_packaged_platform_asset_tree_matches_source_tree() -> None:
    source_root = REPO_ROOT / "platform"
    packaged_root = REPO_ROOT / "src" / "clawops" / "assets" / "platform"

    source_files = _relative_files(source_root)
    packaged_files = _relative_files(packaged_root)

    assert packaged_files == source_files
    for relative_path in sorted(source_files):
        assert (packaged_root / relative_path).read_bytes() == (
            source_root / relative_path
        ).read_bytes()
