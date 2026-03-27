#!/usr/bin/env python3
"""Semantic CLI for fresh-host cache warming."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from clawops.strongclaw_bootstrap import (  # noqa: E402
    install_memory_plugin_asset,
    uv_sync_managed_environment,
)
from clawops.strongclaw_runtime import CommandError  # noqa: E402


def warm_packages(repo_root: Path, *, home_dir: Path | None = None) -> None:
    """Populate the package caches used by fresh-host workflows."""
    resolved_repo_root = repo_root.expanduser().resolve()
    resolved_home_dir = home_dir.expanduser().resolve() if home_dir is not None else None
    uv_sync_managed_environment(resolved_repo_root, home_dir=resolved_home_dir)
    install_memory_plugin_asset(resolved_repo_root)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser(
        "warm-packages",
        help="Populate the package caches needed by fresh-host workflows.",
    )
    package_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    package_parser.add_argument("--home-dir", type=Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested fresh-host cache warming subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "warm-packages":
            warm_packages(
                Path(args.repo_root),
                home_dir=Path(args.home_dir) if args.home_dir is not None else None,
            )
            return 0
    except CommandError as exc:
        print(f"fresh-host-cache error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
