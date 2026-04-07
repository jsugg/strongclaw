#!/usr/bin/env python3
"""Semantic CLI for CI gate workflow orchestration steps."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers._ci_workflows.change_router import (  # noqa: E402
    build_results,
    emit_filters_for_github_output,
    evaluate_filter_matches,
    evaluate_verdict,
    evidence_from_changed_paths,
    evidence_from_output_file_lists,
    load_ci_gate_filters,
    parse_output_file_list,
    render_selection_summary,
    render_verdict_summary,
    selection_from_filter_matches,
    selection_from_output_flags,
    write_github_output,
    write_github_summary,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit_filters = subparsers.add_parser(
        "emit-filters",
        help="Write the gate filter YAML into a multiline GitHub output value.",
    )
    emit_filters.add_argument("--filters-file", type=Path, required=True)
    emit_filters.add_argument("--github-output-file", type=Path)

    summarize = subparsers.add_parser(
        "summarize-selection",
        help="Summarize gate lane selection and emit derived outputs.",
    )
    _add_lane_flag_args(summarize)
    _add_lane_file_args(summarize)
    summarize.add_argument("--filters-file", type=Path)
    summarize.add_argument("--all-changed-paths-files")
    summarize.add_argument("--github-output-file", type=Path)
    summarize.add_argument("--github-summary-file", type=Path)

    verdict = subparsers.add_parser(
        "verdict",
        help="Evaluate final lane results and return a strict CI verdict.",
    )
    _add_lane_flag_args(verdict)
    verdict.add_argument("--classify-result", required=True)
    verdict.add_argument("--docs-parity-result", required=True)
    verdict.add_argument("--harness-result", required=True)
    verdict.add_argument("--compatibility-matrix-result", required=True)
    verdict.add_argument("--memory-plugin-result", required=True)
    verdict.add_argument("--fresh-host-pr-fast-result", required=True)
    verdict.add_argument("--fresh-host-coldstart-result", required=True)
    verdict.add_argument("--security-result", required=True)
    verdict.add_argument("--github-summary-file", type=Path)

    run_docs_parity = subparsers.add_parser(
        "run-docs-parity",
        help="Execute docs parity validation in isolation.",
    )
    run_docs_parity.add_argument("--repo-root", type=Path, required=True)

    return parser.parse_args(argv)


def _add_lane_flag_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--docs-only", required=True)
    parser.add_argument("--fresh-host", required=True)
    parser.add_argument("--fresh-host-coldstart", required=True)
    parser.add_argument("--security", required=True)
    parser.add_argument("--harness", required=True)
    parser.add_argument("--memory-plugin", required=True)
    parser.add_argument("--compatibility-matrix", required=True)


def _add_lane_file_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--docs-only-files", default="[]")
    parser.add_argument("--fresh-host-files", default="[]")
    parser.add_argument("--fresh-host-coldstart-files", default="[]")
    parser.add_argument("--security-files", default="[]")
    parser.add_argument("--harness-files", default="[]")
    parser.add_argument("--memory-plugin-files", default="[]")
    parser.add_argument("--compatibility-matrix-files", default="[]")


def main(argv: list[str] | None = None) -> int:
    """Run the requested CI gate command."""
    args = _parse_args(argv)
    try:
        if args.command == "emit-filters":
            emit_filters_for_github_output(
                filters_file=Path(args.filters_file).expanduser().resolve(),
                github_output_file=(
                    Path(args.github_output_file).expanduser().resolve()
                    if args.github_output_file is not None
                    else None
                ),
            )
            return 0

        selection = selection_from_output_flags(
            docs_only=str(args.docs_only),
            fresh_host=str(args.fresh_host),
            fresh_host_coldstart=str(args.fresh_host_coldstart),
            security=str(args.security),
            harness=str(args.harness),
            memory_plugin=str(args.memory_plugin),
            compatibility_matrix=str(args.compatibility_matrix),
        )

        if args.command == "summarize-selection":
            if args.filters_file is not None and args.all_changed_paths_files is not None:
                filters = load_ci_gate_filters(Path(args.filters_file).expanduser().resolve())
                changed_paths = parse_output_file_list(
                    str(args.all_changed_paths_files),
                    label="all_changed_paths",
                )
                matches = evaluate_filter_matches(filters=filters, changed_paths=changed_paths)
                selection = selection_from_filter_matches(matches)
                evidence = evidence_from_changed_paths(filters=filters, changed_paths=changed_paths)
            else:
                evidence = evidence_from_output_file_lists(
                    docs_only_files=str(args.docs_only_files),
                    fresh_host_files=str(args.fresh_host_files),
                    fresh_host_coldstart_files=str(args.fresh_host_coldstart_files),
                    security_files=str(args.security_files),
                    harness_files=str(args.harness_files),
                    memory_plugin_files=str(args.memory_plugin_files),
                    compatibility_matrix_files=str(args.compatibility_matrix_files),
                )
            if selection.fresh_host_coldstart and selection.fresh_host:
                selection = dataclasses.replace(selection, fresh_host_coldstart=False)
            write_github_output(
                {
                    "docs_only": str(selection.docs_only).lower(),
                    "fresh_host": str(selection.fresh_host).lower(),
                    "fresh_host_coldstart": str(selection.fresh_host_coldstart).lower(),
                    "security": str(selection.security).lower(),
                    "harness": str(selection.harness).lower(),
                    "memory_plugin": str(selection.memory_plugin).lower(),
                    "compatibility_matrix": str(selection.compatibility_matrix).lower(),
                    "any_heavy": str(selection.any_heavy).lower(),
                    "docs_parity_required": str(selection.docs_parity_required).lower(),
                },
                github_output_file=(
                    Path(args.github_output_file).expanduser().resolve()
                    if args.github_output_file is not None
                    else None
                ),
            )
            write_github_summary(
                markdown=render_selection_summary(selection, evidence=evidence),
                github_summary_file=(
                    Path(args.github_summary_file).expanduser().resolve()
                    if args.github_summary_file is not None
                    else None
                ),
            )
            return 0

        if args.command == "verdict":
            results = build_results(
                classify=str(args.classify_result),
                docs_parity=str(args.docs_parity_result),
                harness=str(args.harness_result),
                compatibility_matrix=str(args.compatibility_matrix_result),
                memory_plugin=str(args.memory_plugin_result),
                fresh_host_pr_fast=str(args.fresh_host_pr_fast_result),
                fresh_host_coldstart=str(args.fresh_host_coldstart_result),
                security=str(args.security_result),
            )
            success, failures = evaluate_verdict(selection=selection, results=results)
            write_github_summary(
                markdown=render_verdict_summary(
                    selection=selection,
                    results=results,
                    failures=failures,
                ),
                github_summary_file=(
                    Path(args.github_summary_file).expanduser().resolve()
                    if args.github_summary_file is not None
                    else None
                ),
            )
            return 0 if success else 1

        if args.command == "run-docs-parity":
            repo_root = Path(args.repo_root).expanduser().resolve()
            run_checked(["uv", "sync", "--locked"], cwd=repo_root)
            run_checked(
                [
                    "uv",
                    "run",
                    "pytest",
                    "-q",
                    "tests/suites/contracts/repo/test_docs_parity.py",
                ],
                cwd=repo_root,
            )
            return 0
    except CiWorkflowError as exc:
        print(f"ci-gate error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
