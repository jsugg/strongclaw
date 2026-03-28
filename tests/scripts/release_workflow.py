#!/usr/bin/env python3
"""Semantic CLI for release workflow operations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers._ci_workflows.common import CiWorkflowError  # noqa: E402
from tests.utils.helpers._ci_workflows.release import (  # noqa: E402
    clean_artifact_directories,
    publish_github_release,
    verify_release_artifacts,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    clean_parser = subparsers.add_parser("clean-artifacts", help="Delete release build outputs.")
    clean_parser.add_argument("--path", dest="paths", action="append", type=Path, required=True)

    verify_parser = subparsers.add_parser(
        "verify-artifacts",
        help="Run release artifact verification and install smoke tests.",
    )
    verify_parser.add_argument("--dist-dir", type=Path, required=True)

    publish_parser = subparsers.add_parser(
        "publish-github-release",
        help="Create or update the GitHub release for a tag.",
    )
    publish_parser.add_argument("--tag", required=True)
    publish_parser.add_argument("--dist-dir", type=Path, required=True)
    publish_parser.add_argument("--sbom-path", type=Path, required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested release-workflow subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "clean-artifacts":
            clean_artifact_directories([path.expanduser().resolve() for path in args.paths])
            return 0
        if args.command == "verify-artifacts":
            verify_release_artifacts(Path(args.dist_dir).expanduser().resolve())
            return 0
        if args.command == "publish-github-release":
            publish_github_release(
                str(args.tag),
                Path(args.dist_dir).expanduser().resolve(),
                Path(args.sbom_path).expanduser().resolve(),
            )
            return 0
    except CiWorkflowError as exc:
        print(f"release-workflow error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
