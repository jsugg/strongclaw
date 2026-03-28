#!/usr/bin/env python3
"""Semantic CLI for compatibility-matrix workflow operations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers.ci_workflows import (  # noqa: E402
    CiWorkflowError,
    assert_hypermemory_config,
    assert_lossless_claw_installed,
    prepare_setup_smoke,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare-setup-smoke",
        help="Prepare the compatibility-matrix setup-smoke environment.",
    )
    prepare_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    prepare_parser.add_argument("--runner-temp", type=Path, required=True)
    prepare_parser.add_argument("--github-env-file", type=Path)

    asset_parser = subparsers.add_parser(
        "assert-lossless-claw",
        help="Verify the managed lossless-claw asset exists.",
    )
    asset_parser.add_argument("--tmp-root", type=Path, required=True)

    config_parser = subparsers.add_parser(
        "assert-hypermemory-config",
        help="Verify the rendered hypermemory config contract.",
    )
    config_parser.add_argument("--tmp-root", type=Path, required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested compatibility-matrix subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "prepare-setup-smoke":
            prepare_setup_smoke(
                Path(args.repo_root).expanduser().resolve(),
                Path(args.runner_temp).expanduser().resolve(),
                github_env_file=(
                    Path(args.github_env_file).expanduser().resolve()
                    if args.github_env_file is not None
                    else None
                ),
            )
            return 0
        if args.command == "assert-lossless-claw":
            assert_lossless_claw_installed(Path(args.tmp_root).expanduser().resolve())
            return 0
        if args.command == "assert-hypermemory-config":
            assert_hypermemory_config(Path(args.tmp_root).expanduser().resolve())
            return 0
    except CiWorkflowError as exc:
        print(f"compatibility-matrix error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
