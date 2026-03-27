"""Unit coverage for fresh-host cache warming helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from clawops.strongclaw_runtime import CommandError
from tests.plugins.infrastructure.context import TestContext
from tests.scripts import fresh_host_cache as fresh_host_cache_script


def test_warm_packages_uses_bootstrap_installers(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Package warming should reuse the StrongClaw bootstrap installers."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home_dir = tmp_path / "home"
    seen_calls: list[tuple[str, Path, Path | None]] = []

    test_context.patch.patch_object(
        fresh_host_cache_script,
        "uv_sync_managed_environment",
        new=lambda resolved_repo_root, *, home_dir=None: seen_calls.append(
            ("uv", resolved_repo_root, home_dir)
        ),
    )
    test_context.patch.patch_object(
        fresh_host_cache_script,
        "install_memory_plugin_asset",
        new=lambda resolved_repo_root: seen_calls.append(("npm", resolved_repo_root, None)),
    )

    fresh_host_cache_script.warm_packages(repo_root, home_dir=home_dir)

    assert seen_calls == [
        ("uv", repo_root.resolve(), home_dir.resolve()),
        ("npm", repo_root.resolve(), None),
    ]


def test_main_runs_package_warming(test_context: TestContext, tmp_path: Path) -> None:
    """The cache CLI should dispatch the warm-packages command."""
    seen_calls: list[tuple[Path, Path | None]] = []
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"

    test_context.patch.patch_object(
        fresh_host_cache_script,
        "warm_packages",
        new=lambda resolved_repo_root, *, home_dir=None: seen_calls.append(
            (resolved_repo_root, home_dir)
        ),
    )

    exit_code = fresh_host_cache_script.main(
        ["warm-packages", "--repo-root", str(repo_root), "--home-dir", str(home_dir)]
    )

    assert exit_code == 0
    assert seen_calls == [(repo_root, home_dir)]


def test_main_reports_bootstrap_command_errors(
    test_context: TestContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bootstrap command failures should produce a user-facing nonzero exit."""

    def fail(*args: object, **kwargs: object) -> None:
        raise CommandError("boom")

    test_context.patch.patch_object(fresh_host_cache_script, "warm_packages", new=fail)

    exit_code = fresh_host_cache_script.main(["warm-packages"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "fresh-host-cache error: boom" in captured.err
