"""Shared helpers for distinct CLI root-boundary flags."""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Final

from clawops.root_detection import resolve_project_root, resolve_strongclaw_repo_root
from clawops.runtime_assets import resolve_asset_root

DEPRECATED_REPO_ROOT_FLAG: Final[str] = "--repo-root"


def _warn_deprecated_repo_root(*, command_name: str, replacement_flag: str) -> None:
    """Emit one targeted deprecation warning for the legacy root alias."""
    print(
        f"warning: {DEPRECATED_REPO_ROOT_FLAG} is deprecated for {command_name}; "
        f"use {replacement_flag}.",
        file=sys.stderr,
    )


def _add_path_alias_group(
    parser: argparse.ArgumentParser,
    *,
    canonical_flag: str,
    canonical_dest: str,
    legacy_dest: str,
    help_text: str,
) -> None:
    """Add one canonical path flag plus the hidden legacy alias."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        canonical_flag,
        dest=canonical_dest,
        type=pathlib.Path,
        default=None,
        help=help_text,
    )
    group.add_argument(
        DEPRECATED_REPO_ROOT_FLAG,
        dest=legacy_dest,
        type=pathlib.Path,
        default=None,
        help=argparse.SUPPRESS,
    )


def _coalesce_alias_value(
    *,
    canonical_value: pathlib.Path | None,
    legacy_value: pathlib.Path | None,
    command_name: str,
    canonical_flag: str,
) -> pathlib.Path | None:
    """Return the active path argument and warn on legacy alias use."""
    if legacy_value is not None:
        _warn_deprecated_repo_root(command_name=command_name, replacement_flag=canonical_flag)
        return legacy_value
    return canonical_value


def add_repo_root_argument(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "StrongClaw repo contract root.",
) -> None:
    """Add the canonical StrongClaw repo-contract root flag."""
    parser.add_argument("--repo-root", type=pathlib.Path, default=None, help=help_text)


def add_asset_root_argument(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = (
        "StrongClaw runtime asset root override. Defaults to the packaged asset bundle or "
        "the active source checkout."
    ),
) -> None:
    """Add the canonical runtime asset-root flag and hidden repo-root alias."""
    _add_path_alias_group(
        parser,
        canonical_flag="--asset-root",
        canonical_dest="asset_root",
        legacy_dest="legacy_asset_root",
        help_text=help_text,
    )


def resolve_asset_root_argument(
    args: argparse.Namespace,
    *,
    command_name: str,
) -> pathlib.Path:
    """Resolve the active runtime asset root for one CLI surface."""
    candidate = _coalesce_alias_value(
        canonical_value=getattr(args, "asset_root", None),
        legacy_value=getattr(args, "legacy_asset_root", None),
        command_name=command_name,
        canonical_flag="--asset-root",
    )
    return resolve_asset_root(candidate)


def add_project_root_argument(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "Control project root for run state, planning, and audit outputs.",
) -> None:
    """Add the canonical control-project root flag and hidden repo-root alias."""
    _add_path_alias_group(
        parser,
        canonical_flag="--project-root",
        canonical_dest="project_root",
        legacy_dest="legacy_project_root",
        help_text=help_text,
    )


def resolve_project_root_argument(
    args: argparse.Namespace,
    *,
    command_name: str,
) -> pathlib.Path:
    """Resolve the active control-project root for one CLI surface."""
    candidate = _coalesce_alias_value(
        canonical_value=getattr(args, "project_root", None),
        legacy_value=getattr(args, "legacy_project_root", None),
        command_name=command_name,
        canonical_flag="--project-root",
    )
    return resolve_project_root(candidate)


def add_source_root_argument(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "StrongClaw source checkout root for source-tree verification surfaces.",
) -> None:
    """Add the canonical source-checkout root flag and hidden repo-root alias."""
    _add_path_alias_group(
        parser,
        canonical_flag="--source-root",
        canonical_dest="source_root",
        legacy_dest="legacy_source_root",
        help_text=help_text,
    )


def resolve_source_root_argument(
    args: argparse.Namespace,
    *,
    command_name: str,
) -> pathlib.Path:
    """Resolve the active StrongClaw source-checkout root for one CLI surface."""
    candidate = _coalesce_alias_value(
        canonical_value=getattr(args, "source_root", None),
        legacy_value=getattr(args, "legacy_source_root", None),
        command_name=command_name,
        canonical_flag="--source-root",
    )
    try:
        return resolve_strongclaw_repo_root(candidate, fallback=None)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Could not infer the StrongClaw source checkout root for {command_name}; "
            "pass --source-root explicitly."
        ) from error


def add_ignored_repo_root_alias(parser: argparse.ArgumentParser) -> None:
    """Accept the legacy repo-root flag for compatibility without documenting it."""
    parser.add_argument(
        "--repo-root",
        dest="legacy_repo_root",
        type=pathlib.Path,
        default=None,
        help=argparse.SUPPRESS,
    )


def warn_ignored_repo_root_argument(
    args: argparse.Namespace,
    *,
    command_name: str,
    guidance: str,
) -> None:
    """Warn when a command accepts but ignores the legacy repo-root alias."""
    if getattr(args, "legacy_repo_root", None) is None:
        return
    print(
        f"warning: {DEPRECATED_REPO_ROOT_FLAG} is deprecated for {command_name} and ignored; "
        f"{guidance}",
        file=sys.stderr,
    )
