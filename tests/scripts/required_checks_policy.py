#!/usr/bin/env python3
"""Trusted CLI for auditing live or snapshotted required-check policy."""

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
from tests.utils.helpers._ci_workflows.required_checks import (  # noqa: E402
    audit_branch_protection,
    fetch_branch_protection,
    format_audit_findings,
    load_branch_protection,
    load_required_check_manifest,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "audit-snapshot",
        help="Compare a branch-protection snapshot JSON file with the manifest.",
    )
    snapshot.add_argument(
        "--manifest", type=Path, default=REPO_ROOT / ".github/required-checks.json"
    )
    snapshot.add_argument("--branch-protection-json", type=Path, required=True)

    live = subparsers.add_parser(
        "audit-live",
        help="Fetch branch protection with gh api and compare it with the manifest.",
    )
    live.add_argument("--manifest", type=Path, default=REPO_ROOT / ".github/required-checks.json")
    live.add_argument("--repository")
    live.add_argument("--branch")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a trusted required-check policy audit."""
    args = _parse_args(argv)
    try:
        manifest = load_required_check_manifest(Path(args.manifest))
        if args.command == "audit-snapshot":
            branch_protection = load_branch_protection(Path(args.branch_protection_json))
        else:
            repository = str(args.repository or manifest.repository)
            branch = str(args.branch or manifest.branch)
            branch_protection = fetch_branch_protection(repository=repository, branch=branch)
        findings = audit_branch_protection(
            manifest=manifest,
            branch_protection=branch_protection,
        )
        print(format_audit_findings(findings))
        return 0 if not findings else 1
    except CiWorkflowError as exc:
        print(f"required-check policy error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
