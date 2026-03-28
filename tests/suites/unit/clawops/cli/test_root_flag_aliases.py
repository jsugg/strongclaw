"""Unit tests for CLI root-boundary compatibility aliases."""

from __future__ import annotations

import argparse
import pathlib

import pytest

from clawops.cli_roots import (
    add_asset_root_argument,
    add_ignored_repo_root_alias,
    add_project_root_argument,
    add_source_root_argument,
    resolve_asset_root_argument,
    resolve_project_root_argument,
    resolve_source_root_argument,
    warn_ignored_repo_root_argument,
)
from tests.plugins.infrastructure.context import TestContext


def _init_source_checkout(root: pathlib.Path) -> pathlib.Path:
    """Create the minimum StrongClaw source markers for source-root resolution tests."""
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'strongclaw-test'\n", encoding="utf-8")
    (root / "platform").mkdir()
    (root / "src" / "clawops").mkdir(parents=True)
    return root


def test_asset_root_legacy_repo_root_alias_warns(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = tmp_path / "assets"
    (asset_root / "platform").mkdir(parents=True)
    parser = argparse.ArgumentParser()
    add_asset_root_argument(parser)

    args = parser.parse_args(["--repo-root", str(asset_root)])
    resolved = resolve_asset_root_argument(args, command_name="clawops config")

    assert resolved == asset_root.resolve()
    assert (
        capsys.readouterr().err.strip()
        == "warning: --repo-root is deprecated for clawops config; use --asset-root."
    )


def test_project_root_legacy_repo_root_alias_warns(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser()
    add_project_root_argument(parser)

    args = parser.parse_args(["--repo-root", str(tmp_path)])
    resolved = resolve_project_root_argument(args, command_name="clawops devflow plan")

    assert resolved == tmp_path.resolve()
    assert (
        capsys.readouterr().err.strip()
        == "warning: --repo-root is deprecated for clawops devflow plan; use --project-root."
    )


def test_source_root_requires_source_root_guidance_when_not_discoverable(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    parser = argparse.ArgumentParser()
    add_source_root_argument(parser)
    test_context.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="pass --source-root explicitly\\."):
        resolve_source_root_argument(parser.parse_args([]), command_name="clawops baseline")


def test_source_root_legacy_repo_root_alias_warns(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = _init_source_checkout(tmp_path / "source-root")
    parser = argparse.ArgumentParser()
    add_source_root_argument(parser)

    args = parser.parse_args(["--repo-root", str(source_root)])
    resolved = resolve_source_root_argument(args, command_name="clawops baseline")

    assert resolved == source_root.resolve()
    assert (
        capsys.readouterr().err.strip()
        == "warning: --repo-root is deprecated for clawops baseline; use --source-root."
    )


def test_ignored_repo_root_alias_warns_with_guidance(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser()
    add_ignored_repo_root_alias(parser)

    args = parser.parse_args(["--repo-root", str(tmp_path)])
    warn_ignored_repo_root_argument(
        args,
        command_name="clawops recovery",
        guidance="use the selected backup paths instead.",
    )

    assert (
        capsys.readouterr().err.strip()
        == "warning: --repo-root is deprecated for clawops recovery and ignored; "
        "use the selected backup paths instead."
    )
