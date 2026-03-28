#!/usr/bin/env python3
"""Semantic CLI for memory-plugin workflow operations."""

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
    DEFAULT_OPENCLAW_PACKAGE_SPEC,
    CiWorkflowError,
    run_vendored_host_checks,
    wait_for_qdrant,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    vendored_parser = subparsers.add_parser(
        "run-vendored-host-checks",
        help="Run vendored memory plugin host-functional checks.",
    )
    vendored_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    vendored_parser.add_argument("--package-spec", default=DEFAULT_OPENCLAW_PACKAGE_SPEC)

    qdrant_parser = subparsers.add_parser("wait-for-qdrant", help="Wait for Qdrant readiness.")
    qdrant_parser.add_argument("--url", required=True)
    qdrant_parser.add_argument("--attempts", type=int, default=30)
    qdrant_parser.add_argument("--sleep-seconds", type=float, default=2.0)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested memory-plugin subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "run-vendored-host-checks":
            run_vendored_host_checks(
                Path(args.repo_root).expanduser().resolve(),
                package_spec=str(args.package_spec),
            )
            return 0
        if args.command == "wait-for-qdrant":
            wait_for_qdrant(
                str(args.url),
                attempts=int(args.attempts),
                sleep_seconds=float(args.sleep_seconds),
            )
            return 0
    except CiWorkflowError as exc:
        print(f"memory-plugin-verification error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
