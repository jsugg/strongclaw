#!/usr/bin/env python3
"""Semantic CLI for fresh-host CI orchestration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.utils.helpers.fresh_host import (  # noqa: E402
    FreshHostError,
    cleanup,
    collect_diagnostics,
    prepare_context,
    run_scenario,
    write_summary,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare-context", help="Create a fresh-host context.")
    prepare_parser.add_argument(
        "--scenario",
        required=True,
        choices=("linux", "macos-sidecars", "macos-browser-lab"),
    )
    prepare_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    prepare_parser.add_argument("--runner-temp", type=Path, required=True)
    prepare_parser.add_argument("--workspace", type=Path, default=REPO_ROOT)
    prepare_parser.add_argument("--github-env-file", type=Path)

    run_parser = subparsers.add_parser("run-scenario", help="Run a prepared fresh-host scenario.")
    run_parser.add_argument("--context", type=Path, required=True)

    diagnostics_parser = subparsers.add_parser(
        "collect-diagnostics",
        help="Collect diagnostics for a prepared fresh-host scenario.",
    )
    diagnostics_parser.add_argument("--context", type=Path, required=True)

    cleanup_parser = subparsers.add_parser("cleanup", help="Run best-effort fresh-host cleanup.")
    cleanup_parser.add_argument("--context", type=Path, required=True)

    summary_parser = subparsers.add_parser("write-summary", help="Write one step summary.")
    summary_parser.add_argument("--context", type=Path, required=True)
    summary_parser.add_argument("--summary-file", type=Path, required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested fresh-host subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "prepare-context":
            prepare_context(
                scenario_id=str(args.scenario),
                repo_root=Path(args.repo_root).expanduser().resolve(),
                runner_temp=Path(args.runner_temp).expanduser().resolve(),
                workspace=Path(args.workspace).expanduser().resolve(),
                github_env_file=(
                    Path(args.github_env_file).expanduser().resolve()
                    if args.github_env_file is not None
                    else None
                ),
            )
            return 0
        if args.command == "run-scenario":
            run_scenario(Path(args.context).expanduser().resolve())
            return 0
        if args.command == "collect-diagnostics":
            collect_diagnostics(Path(args.context).expanduser().resolve())
            return 0
        if args.command == "cleanup":
            cleanup(Path(args.context).expanduser().resolve())
            return 0
        if args.command == "write-summary":
            write_summary(
                Path(args.context).expanduser().resolve(),
                Path(args.summary_file).expanduser().resolve(),
            )
            return 0
    except FreshHostError as exc:
        print(f"fresh-host error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
