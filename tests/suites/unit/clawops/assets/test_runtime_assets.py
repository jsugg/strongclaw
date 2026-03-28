"""Tests for packaged StrongClaw runtime asset resolution."""

from __future__ import annotations

import pathlib

from clawops.runtime_assets import PACKAGED_ASSET_ROOT, resolve_asset_path, resolve_runtime_layout
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


def test_runtime_layout_uses_packaged_assets_outside_source_checkout(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    test_context.chdir(tmp_path)

    layout = resolve_runtime_layout(home_dir=tmp_path / "home")

    assert layout.asset_root == PACKAGED_ASSET_ROOT
    assert layout.uses_packaged_assets is True
    assert layout.platform_root == PACKAGED_ASSET_ROOT / "platform"
    assert layout.platform_root.is_dir()


def test_runtime_layout_uses_source_checkout_when_explicit() -> None:
    layout = resolve_runtime_layout(repo_root=REPO_ROOT, home_dir=pathlib.Path.home())

    assert layout.asset_root == REPO_ROOT
    assert layout.source_checkout_root == REPO_ROOT
    assert layout.uses_packaged_assets is False


def test_resolve_asset_path_accepts_explicit_asset_root(tmp_path: pathlib.Path) -> None:
    asset_root = tmp_path / "assets"
    target = asset_root / "platform" / "docs" / "guide.md"
    target.parent.mkdir(parents=True)
    target.write_text("# guide\n", encoding="utf-8")

    assert resolve_asset_path("platform/docs/guide.md", repo_root=asset_root) == target
