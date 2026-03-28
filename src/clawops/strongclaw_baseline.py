"""Python-native baseline verification for StrongClaw."""

from __future__ import annotations

import argparse
import json
import pathlib

from clawops.cli_roots import add_source_root_argument, resolve_source_root_argument
from clawops.runtime_assets import resolve_asset_path, resolve_runtime_layout
from clawops.strongclaw_model_auth import ensure_model_auth
from clawops.strongclaw_runtime import (
    CommandError,
    managed_clawops_command,
    rendered_openclaw_hypermemory_config_path,
    rendered_openclaw_uses_hypermemory,
    require_openclaw,
    resolve_openclaw_config_path,
    run_command,
    run_openclaw_command,
)


def run_harness_smoke(repo_root: pathlib.Path, runs_dir: pathlib.Path) -> None:
    """Run the standard harness smoke suites."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    suites = (
        ("security_regressions.yaml", "security.jsonl"),
        ("policy_regressions.yaml", "policy.jsonl"),
    )
    for suite_name, output_name in suites:
        command = managed_clawops_command(
            repo_root,
            "harness",
            "--suite",
            str(resolve_asset_path(f"platform/configs/harness/{suite_name}", repo_root=repo_root)),
            "--output",
            str(runs_dir / output_name),
        )
        result = run_command(command, cwd=repo_root, timeout_seconds=1800)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or "harness smoke failed"
            raise CommandError(detail, result=None)


def _run_checked(
    repo_root: pathlib.Path, label: str, arguments: list[str], *, timeout_seconds: int = 300
) -> None:
    """Run one OpenClaw command and require success."""
    result = run_openclaw_command(
        repo_root,
        arguments,
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
    )
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or f"{label} failed"
        raise CommandError(detail, result=result)


def verify_baseline(
    repo_root: pathlib.Path,
    *,
    runs_dir: pathlib.Path,
) -> dict[str, object]:
    """Run the baseline verification flow."""
    layout = resolve_runtime_layout(repo_root=repo_root)
    if layout.source_checkout_root is None:
        raise CommandError(
            "baseline verify requires a StrongClaw source checkout because it runs repository tests."
        )
    require_openclaw("Baseline verification runs OpenClaw diagnostics and audits.")
    config_path = resolve_openclaw_config_path(repo_root)
    if not config_path.exists():
        raise CommandError(f"Rendered OpenClaw config not found at {config_path}.")

    _run_checked(repo_root, "OpenClaw doctor", ["doctor", "--non-interactive"])
    _run_checked(repo_root, "OpenClaw security audit", ["security", "audit", "--deep"])
    _run_checked(repo_root, "OpenClaw secrets audit", ["secrets", "audit", "--check"])
    _run_checked(repo_root, "OpenClaw memory status", ["memory", "status", "--deep"])
    _run_checked(
        repo_root,
        "OpenClaw memory search",
        ["memory", "search", "--query", "ClawOps", "--max-results", "1"],
    )

    model_payload = ensure_model_auth(repo_root, check_only=True, probe=False)
    if not bool(model_payload.get("ok")):
        raise CommandError(str(model_payload.get("guidance", "OpenClaw model readiness failed.")))

    hypermemory_payload: dict[str, object] | None = None
    if rendered_openclaw_uses_hypermemory(config_path):
        hypermemory_config_path = rendered_openclaw_hypermemory_config_path(config_path)
        if hypermemory_config_path is None or not hypermemory_config_path.exists():
            raise CommandError(
                "strongclaw-hypermemory is enabled, but its configPath is missing or unreadable."
            )
        status_result = run_command(
            managed_clawops_command(
                repo_root,
                "hypermemory",
                "--config",
                str(hypermemory_config_path),
                "status",
                "--json",
            ),
            cwd=repo_root,
            timeout_seconds=300,
        )
        if not status_result.ok:
            detail = (
                status_result.stderr.strip()
                or status_result.stdout.strip()
                or "hypermemory status failed"
            )
            raise CommandError(detail, result=None)
        hypermemory_payload = json.loads(status_result.stdout or "{}")
        if (
            hypermemory_payload is not None
            and hypermemory_payload.get("backendActive") == "qdrant_sparse_dense_hybrid"
        ):
            verify_result = run_command(
                managed_clawops_command(
                    repo_root,
                    "hypermemory",
                    "--config",
                    str(hypermemory_config_path),
                    "verify",
                    "--json",
                ),
                cwd=repo_root,
                timeout_seconds=300,
            )
            if not verify_result.ok:
                detail = (
                    verify_result.stderr.strip()
                    or verify_result.stdout.strip()
                    or "hypermemory verify failed"
                )
                raise CommandError(detail, result=None)

    tests_result = run_command(
        [
            "uv",
            "run",
            "--project",
            str(layout.source_checkout_root),
            "--locked",
            "--group",
            "dev",
            "pytest",
            "-q",
            str(layout.source_checkout_root / "tests"),
        ],
        cwd=layout.source_checkout_root,
        timeout_seconds=3600,
    )
    if not tests_result.ok:
        detail = (
            tests_result.stderr.strip() or tests_result.stdout.strip() or "repository tests failed"
        )
        raise CommandError(detail, result=None)

    run_harness_smoke(repo_root, runs_dir)

    for target, extra_args in (
        ("sidecars", ["--skip-runtime"]),
        ("observability", ["--skip-runtime"]),
        ("channels", []),
    ):
        result = run_command(
            managed_clawops_command(repo_root, "verify-platform", target, *extra_args),
            cwd=repo_root,
            timeout_seconds=300,
        )
        if not result.ok:
            detail = (
                result.stderr.strip() or result.stdout.strip() or f"{target} verification failed"
            )
            raise CommandError(detail, result=None)

    return {
        "ok": True,
        "config": str(config_path),
        "runsDir": str(runs_dir),
        "modelAuth": model_payload,
        "hypermemory": hypermemory_payload,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the baseline CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_source_root_argument(parser)
    parser.add_argument("--runs-dir", type=pathlib.Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify")
    subparsers.add_parser("harness-smoke")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for baseline verification."""
    args = parse_args(argv)
    repo_root = resolve_source_root_argument(args, command_name="clawops baseline")
    runs_dir = repo_root / ".tmp" / "harness" if args.runs_dir is None else args.runs_dir
    if args.command == "harness-smoke":
        run_harness_smoke(repo_root, runs_dir)
        payload = {"ok": True, "runsDir": str(runs_dir)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    payload = verify_baseline(repo_root, runs_dir=runs_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
