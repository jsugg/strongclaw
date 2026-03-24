"""Top-level CLI dispatcher for the clawops toolkit."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import sys
from collections.abc import Callable
from typing import cast


@dataclasses.dataclass(frozen=True, slots=True)
class CommandSpec:
    """Root CLI command registration."""

    name: str
    import_path: str
    attribute: str
    help_text: str

    def resolve_handler(self) -> Callable[[list[str] | None], int]:
        """Import and return the configured handler."""
        module = importlib.import_module(self.import_path)
        handler = getattr(module, self.attribute)
        if not callable(handler):
            raise TypeError(f"{self.import_path}.{self.attribute} is not callable")
        return cast(Callable[[list[str] | None], int], handler)


WRAPPER_COMMANDS: dict[str, tuple[str, str]] = {
    "github": ("clawops.wrappers.github", "main"),
    "webhook": ("clawops.wrappers.webhook", "main"),
}


def _dispatch_wrapper(argv: list[str] | None) -> int:
    """Dispatch wrapper commands."""
    args = [] if argv is None else list(argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: clawops wrapper {github|webhook} [args...]")
        return 0 if args else 1
    wrapper = args.pop(0)
    target = WRAPPER_COMMANDS.get(wrapper)
    if target is None:
        print(f"unknown wrapper: {wrapper}")
        return 2
    module = importlib.import_module(target[0])
    handler = getattr(module, target[1])
    return handler(args)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("merge-json", "clawops.json_merge", "main", "Merge JSON config overlays."),
    CommandSpec(
        "config", "clawops.config_cli", "main", "Manage StrongClaw-owned OpenClaw config profiles."
    ),
    CommandSpec(
        "render-openclaw-config",
        "clawops.openclaw_config",
        "main",
        "Render OpenClaw config profiles and placeholder-backed overlays.",
    ),
    CommandSpec("approvals", "clawops.approvals", "main", "Manage the review and approval queue."),
    CommandSpec("op-journal", "clawops.op_journal", "main", "Manage the SQLite operation journal."),
    CommandSpec(
        "policy", "clawops.policy_engine", "main", "Evaluate policy payloads against YAML rules."
    ),
    CommandSpec(
        "context", "clawops.context_service", "main", "Index, query, or pack repository context."
    ),
    CommandSpec(
        "memory",
        "clawops.memory_tools",
        "main",
        "Migrate and verify the hypermemory to memory-pro transition.",
    ),
    CommandSpec(
        "repo", "clawops.repo_tools", "repo_main", "Validate the repo/upstream workspace contract."
    ),
    CommandSpec(
        "setup", "clawops.setup_cli", "setup_main", "Run the guided StrongClaw setup workflow."
    ),
    CommandSpec(
        "doctor", "clawops.setup_cli", "doctor_main", "Run a deep StrongClaw readiness scan."
    ),
    CommandSpec(
        "doctor-host",
        "clawops.setup_cli",
        "doctor_host_main",
        "Run the host-only StrongClaw doctor.",
    ),
    CommandSpec(
        "bootstrap",
        "clawops.strongclaw_bootstrap",
        "main",
        "Bootstrap the StrongClaw host and managed environment.",
    ),
    CommandSpec(
        "varlock-env",
        "clawops.strongclaw_varlock_env",
        "main",
        "Create, normalize, and validate the StrongClaw env contract.",
    ),
    CommandSpec(
        "model-auth",
        "clawops.strongclaw_model_auth",
        "main",
        "Ensure the rendered OpenClaw config has a usable model chain.",
    ),
    CommandSpec(
        "services",
        "clawops.strongclaw_services",
        "main",
        "Render and activate host service definitions.",
    ),
    CommandSpec(
        "ops",
        "clawops.strongclaw_ops",
        "main",
        "Control the OpenClaw gateway, sidecars, and compose state.",
    ),
    CommandSpec(
        "baseline",
        "clawops.strongclaw_baseline",
        "main",
        "Run the StrongClaw baseline verification gate.",
    ),
    CommandSpec(
        "recovery",
        "clawops.strongclaw_recovery",
        "main",
        "Create, verify, restore, and prune backup archives.",
    ),
    CommandSpec(
        "worktree",
        "clawops.repo_tools",
        "worktree_main",
        "List, create, or prune managed git worktrees.",
    ),
    CommandSpec(
        "skills", "clawops.skill_scanner", "main", "Scan and promote staged skill bundles."
    ),
    CommandSpec(
        "skill-scan",
        "clawops.skill_scanner",
        "main",
        "Legacy alias for skills scan and quarantine workflows.",
    ),
    CommandSpec("harness", "clawops.harness", "main", "Run YAML-driven regression suites."),
    CommandSpec("charts", "clawops.charts", "main", "Render charts from harness results."),
    CommandSpec(
        "allowlists", "clawops.allowlist_sync", "main", "Normalize and render channel allowlists."
    ),
    CommandSpec(
        "workflow", "clawops.workflow_runner", "main", "Run deterministic operational workflows."
    ),
    CommandSpec(
        "acp-runner", "clawops.acp_runner", "main", "Run ACP sessions with locking and summaries."
    ),
    CommandSpec(
        "verify-platform",
        "clawops.platform_verify",
        "main",
        "Verify sidecars, observability, and channels.",
    ),
    CommandSpec(
        "hypermemory",
        "clawops.hypermemory.cli",
        "main",
        "Run the Markdown-canonical sparse+dense hypermemory engine.",
    ),
    CommandSpec(
        "supply-chain",
        "clawops.supply_chain",
        "main",
        "Inventory and refresh pinned workflows, compose digests, and proposal branches.",
    ),
)


def _build_root_parser() -> argparse.ArgumentParser:
    """Create the root help parser."""
    epilog_lines = ["available commands:"]
    for spec in COMMANDS:
        epilog_lines.append(f"  {spec.name:<15} {spec.help_text}")
    epilog_lines.append("  wrapper         Run policy-gated external wrappers.")
    return argparse.ArgumentParser(
        prog="clawops",
        usage="clawops <command> [args ...]",
        description="Companion ops, policy, context, and harness tooling for OpenClaw.",
        epilog="\n".join(epilog_lines),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )


def main(argv: list[str] | None = None) -> int:
    """Dispatch to subcommands."""
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_root_parser()
    if not args:
        parser.print_help()
        return 1
    if args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command = args.pop(0)
    if command == "wrapper":
        return _dispatch_wrapper(args)
    for spec in COMMANDS:
        if spec.name == command:
            return spec.resolve_handler()(args)
    print(f"unknown command: {command}")
    return 2
