"""Python-native baseline verification for StrongClaw."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
from typing import cast

from clawops.cli_roots import add_source_root_argument, resolve_source_root_argument
from clawops.runtime_assets import resolve_asset_path, resolve_runtime_layout
from clawops.strongclaw_model_auth import ensure_model_auth
from clawops.strongclaw_runtime import (
    READINESS_VARLOCK_ENV_MODES,
    CommandError,
    VarlockEnvMode,
    rendered_openclaw_hypermemory_config_path,
    rendered_openclaw_uses_hypermemory,
    require_openclaw,
    resolve_openclaw_config_path,
    run_command,
    run_managed_clawops_command,
    run_openclaw_command,
    use_varlock_env_mode,
)

_BASELINE_REPO_TEST_IGNORE_PATHS: tuple[str, ...] = (
    "tests/suites/contracts/repo/launch_readiness",
)


def run_harness_smoke(repo_root: pathlib.Path, runs_dir: pathlib.Path) -> None:
    """Run the standard harness smoke suites."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    suites = (
        ("security_regressions.yaml", "security.jsonl"),
        ("policy_regressions.yaml", "policy.jsonl"),
    )
    for suite_name, output_name in suites:
        result = run_managed_clawops_command(
            repo_root,
            [
                "harness",
                "--suite",
                str(
                    resolve_asset_path(
                        f"platform/configs/harness/{suite_name}",
                        repo_root=repo_root,
                    )
                ),
                "--output",
                str(runs_dir / output_name),
            ],
            cwd=repo_root,
            timeout_seconds=1800,
        )
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


def _status_count(payload: dict[str, object], key: str) -> int:
    """Return one integer status counter from a hypermemory status payload."""
    raw_value = payload.get(key)
    if isinstance(raw_value, bool):
        return 0
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value)
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return 0
        try:
            return int(stripped)
        except ValueError:
            return 0
    return 0


def _hypermemory_needs_reindex(payload: dict[str, object]) -> bool:
    """Return whether baseline should run an eager hypermemory reindex."""
    if bool(payload.get("dirty")):
        return True
    if _status_count(payload, "vectorItems") <= 0:
        return True
    if _status_count(payload, "sparseVectorItems") <= 0:
        return True
    return False


def _run_hypermemory_status(
    repo_root: pathlib.Path,
    *,
    hypermemory_config_path: pathlib.Path,
) -> dict[str, object]:
    """Run hypermemory status and return the parsed payload."""
    status_result = run_managed_clawops_command(
        repo_root,
        [
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "status",
            "--json",
        ],
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
    return cast(dict[str, object], json.loads(status_result.stdout or "{}"))


def verify_baseline(
    repo_root: pathlib.Path,
    *,
    runs_dir: pathlib.Path,
    degraded: bool = False,
    exclude_browser_lab: bool = False,
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

    model_payload = ensure_model_auth(repo_root, check_only=True, probe=not degraded)
    if not bool(model_payload.get("ok")):
        raise CommandError(str(model_payload.get("guidance", "OpenClaw model readiness failed.")))

    hypermemory_payload: dict[str, object] | None = None
    if rendered_openclaw_uses_hypermemory(config_path):
        hypermemory_config_path = rendered_openclaw_hypermemory_config_path(config_path)
        if hypermemory_config_path is None or not hypermemory_config_path.exists():
            raise CommandError(
                "strongclaw-hypermemory is enabled, but its configPath is missing or unreadable."
            )
        hypermemory_payload = _run_hypermemory_status(
            repo_root,
            hypermemory_config_path=hypermemory_config_path,
        )
        if _hypermemory_needs_reindex(hypermemory_payload):
            index_result = run_managed_clawops_command(
                repo_root,
                [
                    "hypermemory",
                    "--config",
                    str(hypermemory_config_path),
                    "index",
                    "--json",
                ],
                cwd=repo_root,
                timeout_seconds=600,
            )
            if not index_result.ok:
                detail = (
                    index_result.stderr.strip()
                    or index_result.stdout.strip()
                    or "hypermemory index failed"
                )
                raise CommandError(detail, result=None)
            hypermemory_payload = _run_hypermemory_status(
                repo_root,
                hypermemory_config_path=hypermemory_config_path,
            )
        if hypermemory_payload.get("backendActive") == "qdrant_sparse_dense_hybrid":
            verify_result = run_managed_clawops_command(
                repo_root,
                [
                    "hypermemory",
                    "--config",
                    str(hypermemory_config_path),
                    "verify",
                    "--json",
                ],
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

    pytest_home_parent = repo_root / ".tmp"
    pytest_home_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="baseline-pytest-home-",
        dir=pytest_home_parent,
    ) as isolated_home_dir:
        isolated_home = pathlib.Path(isolated_home_dir)
        pytest_env = dict(os.environ)
        pytest_env["HOME"] = str(isolated_home)
        pytest_env["XDG_CONFIG_HOME"] = str(isolated_home / ".config")
        pytest_env["XDG_DATA_HOME"] = str(isolated_home / ".local" / "share")
        pytest_env["XDG_STATE_HOME"] = str(isolated_home / ".local" / "state")
        for key in (
            "OPENCLAW_HOME",
            "OPENCLAW_STATE_DIR",
            "OPENCLAW_CONFIG_PATH",
            "OPENCLAW_CONFIG",
            "OPENCLAW_PROFILE",
            "STRONGCLAW_RUNTIME_ROOT",
            "STRONGCLAW_CONFIG_DIR",
            "STRONGCLAW_DATA_DIR",
            "STRONGCLAW_STATE_DIR",
            "STRONGCLAW_LOG_DIR",
            "STRONGCLAW_MEMORY_CONFIG_DIR",
            "STRONGCLAW_VARLOCK_ENV_PATH",
            "STRONGCLAW_VARLOCK_ENV_MODE",
            "OPENCLAW_VARLOCK_ENV_MODE",
            "VARLOCK_ENV_DIR",
            "VARLOCK_LOCAL_ENV_FILE",
        ):
            pytest_env.pop(key, None)
        pytest_command: list[str] = [
            "uv",
            "run",
            "--project",
            str(layout.source_checkout_root),
            "--locked",
            "--group",
            "dev",
            "pytest",
            "-q",
        ]
        for ignore_path in _BASELINE_REPO_TEST_IGNORE_PATHS:
            pytest_command.extend(["--ignore", str(layout.source_checkout_root / ignore_path)])
        pytest_command.append(str(layout.source_checkout_root / "tests"))
        tests_result = run_command(
            pytest_command,
            cwd=layout.source_checkout_root,
            env=pytest_env,
            timeout_seconds=3600,
        )
    if not tests_result.ok:
        detail = (
            tests_result.stderr.strip() or tests_result.stdout.strip() or "repository tests failed"
        )
        raise CommandError(detail, result=None)

    run_harness_smoke(repo_root, runs_dir)

    verification_targets: list[tuple[str, list[str]]] = [
        ("sidecars", ["--skip-runtime"] if degraded else []),
        ("observability", ["--skip-runtime"] if degraded else []),
        ("channels", []),
    ]
    if not exclude_browser_lab:
        verification_targets.append(("browser-lab", ["--skip-runtime"] if degraded else []))
    for target, extra_args in verification_targets:
        result = run_managed_clawops_command(
            repo_root,
            ["verify-platform", target, *extra_args],
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
        "degraded": degraded,
        "excludeBrowserLab": exclude_browser_lab,
        "includeBrowserLab": not exclude_browser_lab,
        "runsDir": str(runs_dir),
        "verificationMode": "degraded" if degraded else "runtime",
        "modelAuth": model_payload,
        "hypermemory": hypermemory_payload,
        "guidance": (
            "Runtime probes were skipped for model auth, sidecars, and observability. "
            "Rerun `clawops baseline verify` for full release-readiness evidence."
            if degraded
            else "Runtime probes passed."
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the baseline CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_source_root_argument(parser)
    parser.add_argument("--runs-dir", type=pathlib.Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument(
        "--degraded",
        action="store_true",
        help="Skip runtime probes and keep the output explicitly marked as degraded.",
    )
    verify_parser.add_argument(
        "--env-mode",
        choices=READINESS_VARLOCK_ENV_MODES,
        default="managed",
        help="Varlock env source for readiness checks (default: managed).",
    )
    verify_parser.add_argument(
        "--exclude-browser-lab",
        action="store_true",
        help="Exclude browser-lab verification from the baseline gate.",
    )
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
    env_mode = cast(VarlockEnvMode, str(args.env_mode))
    with use_varlock_env_mode(env_mode):
        payload = verify_baseline(
            repo_root,
            runs_dir=runs_dir,
            degraded=bool(args.degraded),
            exclude_browser_lab=bool(args.exclude_browser_lab),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
