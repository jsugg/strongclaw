#!/usr/bin/env python3
"""Reject AI-tool attribution in commits, pushed refs, and pull-request metadata."""

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

from tests.utils.helpers._ci_workflows.attribution import (  # noqa: E402
    AttributionFinding,
    PushTarget,
    fetch_pull_request_snapshot,
    format_findings,
    load_commit_message,
    pre_push_target_from_env,
    push_rev_args,
    scan_pull_request,
    scan_push,
    scan_value,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    commit_msg = subparsers.add_parser("commit-msg", help="Check one git commit-msg file.")
    commit_msg.add_argument("message_file", type=Path)

    pre_push = subparsers.add_parser(
        "pre-push",
        help="Check the pushed ref and its new commits from the pre-commit pre-push environment.",
    )
    pre_push.add_argument("--repo-root", type=Path, default=Path.cwd())

    pull_request = subparsers.add_parser(
        "pull-request",
        help="Refetch one pull request through the API and check its metadata and commits.",
    )
    pull_request.add_argument("--repository", required=True)
    pull_request.add_argument("--number", type=int, required=True)

    push = subparsers.add_parser("push", help="Check the ref and commits of one pushed update.")
    push.add_argument("--ref", required=True)
    push.add_argument("--before", default="")
    push.add_argument("--after", required=True)
    push.add_argument("--repo-root", type=Path, default=Path.cwd())

    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> tuple[list[AttributionFinding], str]:
    """Execute one subcommand and return its findings plus a scope label."""
    if args.command == "commit-msg":
        message = load_commit_message(Path(args.message_file))
        finding = scan_value("commit-message", field="commit message", value=message)
        return ([] if finding is None else [finding]), "commit message"

    if args.command == "pre-push":
        target = pre_push_target_from_env(os.environ)
        return scan_push(target, cwd=Path(args.repo_root)), f"push to {target.remote_ref}"

    if args.command == "pull-request":
        token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
        snapshot = fetch_pull_request_snapshot(
            repository=str(args.repository),
            number=int(args.number),
            token=token,
        )
        scope = f"pull request #{snapshot.number}, {len(snapshot.commits)} commit(s)"
        return scan_pull_request(snapshot), scope

    target = PushTarget(
        remote_ref=str(args.ref),
        rev_args=push_rev_args(before=str(args.before), after=str(args.after)),
    )
    return scan_push(target, cwd=Path(args.repo_root)), f"push to {target.remote_ref}"


def main(argv: list[str] | None = None) -> int:
    """Run the attribution guard and exit non-zero on any finding or error."""
    args = _parse_args(argv)
    try:
        findings, scope = _run(args)
    except CiWorkflowError as exc:
        print(f"attribution guard error: {exc}", file=sys.stderr)
        return 1
    print(format_findings(findings, scope=scope), file=sys.stderr if findings else sys.stdout)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
