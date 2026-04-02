#!/usr/bin/env python3
"""Semantic CLI for security workflow operations."""

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

from tests.utils.helpers._ci_workflows.common import CiWorkflowError  # noqa: E402
from tests.utils.helpers._ci_workflows.security import (  # noqa: E402
    append_coverage_summary,
    enforce_coverage_thresholds,
    enforce_independent_review,
    install_gitleaks,
    install_syft,
    run_recovery_smoke,
    verify_channels_contract,
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

    coverage_gate_parser = subparsers.add_parser(
        "enforce-coverage-thresholds",
        help="Fail when overall or critical-module coverage drops below policy floors.",
    )
    coverage_gate_parser.add_argument("--coverage-file", type=Path, required=True)

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

    channels_parser = subparsers.add_parser(
        "verify-channels-contract",
        help="Validate shipped channels/docs/allowlist parity in one semantic command.",
    )
    channels_parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
    )

    recovery_parser = subparsers.add_parser(
        "run-recovery-smoke",
        help="Exercise backup-create, backup-verify, and restore against a disposable home.",
    )
    recovery_parser.add_argument(
        "--tmp-root",
        type=Path,
        required=True,
    )

    review_parser = subparsers.add_parser(
        "enforce-independent-review",
        help="Require one non-author approval for security-critical pull-request changes.",
    )
    review_parser.add_argument(
        "--event-path",
        type=Path,
        default=None,
        help="Path to the GitHub event payload. Defaults to $GITHUB_EVENT_PATH.",
    )
    review_parser.add_argument(
        "--repository",
        default=None,
        help="Repository slug (owner/repo). Defaults to $GITHUB_REPOSITORY.",
    )
    review_parser.add_argument(
        "--github-api-base",
        default="https://api.github.com",
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
        if args.command == "enforce-coverage-thresholds":
            enforce_coverage_thresholds(Path(args.coverage_file).expanduser().resolve())
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
        if args.command == "verify-channels-contract":
            verify_channels_contract(
                repo_root=Path(args.repo_root).expanduser().resolve(),
            )
            return 0
        if args.command == "run-recovery-smoke":
            run_recovery_smoke(
                tmp_root=Path(args.tmp_root).expanduser().resolve(),
            )
            return 0
        if args.command == "enforce-independent-review":
            event_path_text = args.event_path or os.environ.get("GITHUB_EVENT_PATH")
            repository = args.repository or os.environ.get("GITHUB_REPOSITORY")
            github_token = os.environ.get("GITHUB_TOKEN", "")
            if event_path_text is None:
                raise CiWorkflowError(
                    "missing event payload path: set --event-path or GITHUB_EVENT_PATH"
                )
            if repository is None:
                raise CiWorkflowError("missing repository: set --repository or GITHUB_REPOSITORY")
            enforce_independent_review(
                event_path=Path(event_path_text).expanduser().resolve(),
                repository=str(repository),
                github_token=github_token,
                github_api_base=str(args.github_api_base),
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
