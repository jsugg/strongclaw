"""Helpers for memory-plugin verification workflows."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked

DEFAULT_OPENCLAW_PACKAGE_SPEC = "openclaw@2026.3.13"
AWS_CREDENTIAL_ENV_VARS = (
    "AWS_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
)


def run_vendored_host_checks(
    repo_root: Path,
    *,
    package_spec: str = DEFAULT_OPENCLAW_PACKAGE_SPEC,
) -> None:
    """Run the vendored memory plugin host-functional verification."""
    resolved_repo_root = repo_root.expanduser().resolve()
    tool_dir = Path(tempfile.mkdtemp(prefix="strongclaw-openclaw-cli."))
    try:
        run_checked(
            [
                "npm",
                "install",
                "--prefix",
                str(tool_dir),
                "--no-fund",
                "--no-audit",
                package_spec,
            ],
            cwd=resolved_repo_root,
        )
        env = dict(os.environ)
        env["PATH"] = f"{tool_dir / 'node_modules' / '.bin'}:{env.get('PATH', '')}"
        for key in AWS_CREDENTIAL_ENV_VARS:
            env.pop(key, None)
        plugin_dir = resolved_repo_root / "platform" / "plugins" / "memory-lancedb-pro"
        run_checked(["npm", "ci", "--no-fund", "--no-audit"], cwd=plugin_dir, env=env)
        run_checked(["npm", "run", "test:openclaw-host"], cwd=plugin_dir, env=env)
    finally:
        shutil.rmtree(tool_dir, ignore_errors=True)


def wait_for_qdrant(url: str, *, attempts: int = 30, sleep_seconds: float = 2.0) -> None:
    """Poll a Qdrant health endpoint until it succeeds."""
    if attempts < 1:
        raise CiWorkflowError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError):
            if attempt >= attempts:
                break
            time.sleep(sleep_seconds)
            continue
        if attempt >= attempts:
            break
        time.sleep(sleep_seconds)
    raise CiWorkflowError(f"Qdrant failed to become ready at {url} after {attempts} attempts")


def run_clawops_memory_migration(
    repo_root: Path,
    *,
    runner_temp: Path | None = None,
) -> Path:
    """Run a dry-run clawops memory migration and return the report path."""
    resolved_repo_root = repo_root.expanduser().resolve()
    report_root = (
        runner_temp.expanduser().resolve()
        if runner_temp is not None
        else Path(tempfile.mkdtemp(prefix="strongclaw-memory-migration."))
    )
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "clawops-memory-migration-report.json"
    env = dict(os.environ)
    if not env.get("PYTHONPATH", "").strip():
        env["PYTHONPATH"] = "src"
    run_checked(
        [
            "uv",
            "run",
            "python",
            "-m",
            "clawops",
            "memory",
            "migrate-hypermemory-to-pro",
            "--dry-run",
            "--report",
            str(report_path),
        ],
        cwd=resolved_repo_root,
        env=env,
    )
    return report_path
