#!/usr/bin/env python3
"""Semantic CLI for fresh-host CI orchestration."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from clawops.memory_profiles import MANAGED_MEMORY_PROFILE_IDS  # noqa: E402
from tests.utils.helpers.fresh_host import (  # noqa: E402
    SCENARIO_SPECS,
    FreshHostError,
    cleanup,
    collect_diagnostics,
    prepare_context,
    preview_context,
    record_job_budget,
    run_scenario,
    write_summary,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    budget_parser = subparsers.add_parser(
        "initialize-budget",
        help="Record the non-resettable job deadline and phase reserves.",
    )
    budget_parser.add_argument("--job-timeout-minutes", type=int, required=True)
    budget_parser.add_argument("--scenario-reserve-minutes", type=int, required=True)
    budget_parser.add_argument("--observer-reserve-minutes", type=int, required=True)
    budget_parser.add_argument("--github-env-file", type=Path)

    prepare_parser = subparsers.add_parser("prepare-context", help="Create a fresh-host context.")
    prepare_parser.add_argument(
        "--scenario",
        required=True,
        choices=tuple(SCENARIO_SPECS),
    )
    prepare_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    prepare_parser.add_argument(
        "--profile",
        default="openclaw-default",
        choices=MANAGED_MEMORY_PROFILE_IDS,
    )
    prepare_parser.add_argument("--runner-temp", type=Path, required=True)
    prepare_parser.add_argument("--workspace", type=Path, default=REPO_ROOT)
    prepare_parser.add_argument("--github-env-file", type=Path)

    preview_parser = subparsers.add_parser(
        "preview-context",
        help="Render a context preview for the prepared scenario.",
    )
    preview_parser.add_argument("--context", type=Path, required=True)
    preview_parser.add_argument("--summary-file", type=Path)

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
        if args.command == "initialize-budget":
            budget = record_job_budget(
                job_timeout_minutes=int(args.job_timeout_minutes),
                scenario_reserve_minutes=int(args.scenario_reserve_minutes),
                observer_reserve_minutes=int(args.observer_reserve_minutes),
                environ=os.environ,
                github_env_file=(
                    Path(args.github_env_file).expanduser().resolve()
                    if args.github_env_file is not None
                    else None
                ),
            )
            print(f"fresh-host job budget: {budget.to_env()}")
            return 0
        if args.command == "prepare-context":
            prepare_context(
                scenario_id=str(args.scenario),
                profile=str(args.profile),
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
        if args.command == "preview-context":
            preview_context(
                Path(args.context).expanduser().resolve(),
                summary_file=(
                    Path(args.summary_file).expanduser().resolve()
                    if args.summary_file is not None
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
