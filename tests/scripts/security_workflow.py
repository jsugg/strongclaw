#!/usr/bin/env python3
"""Semantic CLI for security workflow operations."""

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
from tests.utils.helpers._ci_workflows.security import (  # noqa: E402
    append_coverage_summary,
    install_gitleaks,
    install_syft,
    write_empty_sarif,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser(
        "write-coverage-summary",
        help="Append line coverage to the GitHub step summary.",
    )
    summary_parser.add_argument("--coverage-file", type=Path, required=True)
    summary_parser.add_argument("--summary-file", type=Path, required=True)

    gitleaks_parser = subparsers.add_parser("install-gitleaks", help="Install pinned gitleaks.")
    gitleaks_parser.add_argument("--version", required=True)
    gitleaks_parser.add_argument("--sha256", required=True)
    gitleaks_parser.add_argument("--runner-temp", type=Path, required=True)
    gitleaks_parser.add_argument("--github-path-file", type=Path)

    syft_parser = subparsers.add_parser("install-syft", help="Install pinned syft.")
    syft_parser.add_argument("--version", required=True)
    syft_parser.add_argument("--sha256", required=True)
    syft_parser.add_argument("--runner-temp", type=Path, required=True)
    syft_parser.add_argument("--github-path-file", type=Path)

    sarif_parser = subparsers.add_parser(
        "write-empty-sarif",
        help="Write the placeholder SARIF file used for historical categories.",
    )
    sarif_parser.add_argument("--output", type=Path, required=True)
    sarif_parser.add_argument(
        "--information-uri",
        default="https://github.com/jsugg/strongclaw",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested security-workflow subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "write-coverage-summary":
            append_coverage_summary(
                Path(args.coverage_file).expanduser().resolve(),
                Path(args.summary_file).expanduser().resolve(),
            )
            return 0
        if args.command == "install-gitleaks":
            install_gitleaks(
                version=str(args.version),
                sha256=str(args.sha256),
                runner_temp=Path(args.runner_temp).expanduser().resolve(),
                github_path_file=(
                    Path(args.github_path_file).expanduser().resolve()
                    if args.github_path_file is not None
                    else None
                ),
            )
            return 0
        if args.command == "install-syft":
            install_syft(
                version=str(args.version),
                sha256=str(args.sha256),
                runner_temp=Path(args.runner_temp).expanduser().resolve(),
                github_path_file=(
                    Path(args.github_path_file).expanduser().resolve()
                    if args.github_path_file is not None
                    else None
                ),
            )
            return 0
        if args.command == "write-empty-sarif":
            write_empty_sarif(
                Path(args.output).expanduser().resolve(),
                information_uri=str(args.information_uri),
            )
            return 0
    except CiWorkflowError as exc:
        print(f"security-workflow error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
