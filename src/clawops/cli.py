
"""Top-level CLI dispatcher for the clawops toolkit."""

from __future__ import annotations

import sys

from clawops import (
    allowlist_sync,
    charts,
    context_service,
    harness,
    json_merge,
    op_journal,
    policy_engine,
    skill_scanner,
    workflow_runner,
)
from clawops.wrappers import github as github_wrapper
from clawops.wrappers import jira as jira_wrapper
from clawops.wrappers import webhook as webhook_wrapper


def main(argv: list[str] | None = None) -> int:
    """Dispatch to subcommands.

    The dispatcher is intentionally simple so subcommands can own their own
    argument parsing, including `--help`.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: clawops <command> [args...]")
        print("commands: merge-json, op-journal, policy, context, skill-scan, harness, charts, allowlists, workflow, wrapper {github|jira|webhook}")
        return 1

    command = args.pop(0)
    if command == "merge-json":
        return json_merge.main(args)
    if command == "op-journal":
        return op_journal.main(args)
    if command == "policy":
        return policy_engine.main(args)
    if command == "context":
        return context_service.main(args)
    if command == "skill-scan":
        return skill_scanner.main(args)
    if command == "harness":
        return harness.main(args)
    if command == "charts":
        return charts.main(args)
    if command == "allowlists":
        return allowlist_sync.main(args)
    if command == "workflow":
        return workflow_runner.main(args)
    if command == "wrapper":
        if not args:
            print("usage: clawops wrapper {github|jira|webhook} [args...]")
            return 1
        wrapper = args.pop(0)
        if wrapper == "github":
            return github_wrapper.main(args)
        if wrapper == "jira":
            return jira_wrapper.main(args)
        if wrapper == "webhook":
            return webhook_wrapper.main(args)
        print(f"unknown wrapper: {wrapper}")
        return 1
    print(f"unknown command: {command}")
    return 1
