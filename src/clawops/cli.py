"""Top-level CLI dispatcher for the clawops toolkit."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable

from clawops import (
    acp_runner,
    allowlist_sync,
    approvals,
    charts,
    context_service,
    harness,
    json_merge,
    memory_v2,
    op_journal,
    openclaw_config,
    platform_verify,
    policy_engine,
    skill_scanner,
    workflow_runner,
)
from clawops.wrappers import github as github_wrapper
from clawops.wrappers import webhook as webhook_wrapper


@dataclasses.dataclass(frozen=True, slots=True)
class CommandSpec:
    """Root CLI command registration."""

    name: str
    handler: Callable[[list[str] | None], int]
    help_text: str


WRAPPER_COMMANDS: dict[str, Callable[[list[str] | None], int]] = {
    "github": github_wrapper.main,
    "webhook": webhook_wrapper.main,
}


def _dispatch_wrapper(argv: list[str] | None) -> int:
    """Dispatch wrapper commands."""
    args = [] if argv is None else list(argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: clawops wrapper {github|webhook} [args...]")
        return 0 if args else 1
    wrapper = args.pop(0)
    handler = WRAPPER_COMMANDS.get(wrapper)
    if handler is None:
        print(f"unknown wrapper: {wrapper}")
        return 2
    return handler(args)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("merge-json", json_merge.main, "Merge JSON config overlays."),
    CommandSpec(
        "render-openclaw-config",
        openclaw_config.main,
        "Render OpenClaw config profiles and placeholder-backed overlays.",
    ),
    CommandSpec("approvals", approvals.main, "Manage the review and approval queue."),
    CommandSpec("op-journal", op_journal.main, "Manage the SQLite operation journal."),
    CommandSpec("policy", policy_engine.main, "Evaluate policy payloads against YAML rules."),
    CommandSpec("context", context_service.main, "Index, query, or pack repository context."),
    CommandSpec("skill-scan", skill_scanner.main, "Scan skill bundles for suspicious patterns."),
    CommandSpec("harness", harness.main, "Run YAML-driven regression suites."),
    CommandSpec("charts", charts.main, "Render charts from harness results."),
    CommandSpec("allowlists", allowlist_sync.main, "Normalize and render channel allowlists."),
    CommandSpec("workflow", workflow_runner.main, "Run deterministic operational workflows."),
    CommandSpec("acp-runner", acp_runner.main, "Run ACP sessions with locking and summaries."),
    CommandSpec(
        "verify-platform", platform_verify.main, "Verify sidecars, observability, and channels."
    ),
    CommandSpec("memory-v2", memory_v2.main, "Run the opt-in Markdown-canonical memory v2 engine."),
    CommandSpec("wrapper", _dispatch_wrapper, "Run policy-gated external wrappers."),
)


def _build_root_parser() -> argparse.ArgumentParser:
    """Create the root help parser."""
    epilog_lines = ["available commands:"]
    for spec in COMMANDS:
        epilog_lines.append(f"  {spec.name:<11} {spec.help_text}")
    return argparse.ArgumentParser(
        prog="clawops",
        usage="clawops <command> [args ...]",
        description="Companion ops, policy, context, and harness tooling for OpenClaw.",
        epilog="\n".join(epilog_lines),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )


def main(argv: list[str] | None = None) -> int:
    """Dispatch to subcommands.

    The dispatcher is intentionally simple so subcommands can own their own
    argument parsing, including `--help`.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_root_parser()
    if not args:
        parser.print_help()
        return 1
    if args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command = args.pop(0)
    for spec in COMMANDS:
        if spec.name == command:
            return spec.handler(args)
    print(f"unknown command: {command}")
    return 2
