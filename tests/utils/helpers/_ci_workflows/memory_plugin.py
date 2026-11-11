"""Helpers for memory-plugin verification workflows."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from clawops.strongclaw_node_tools import (
    LocalPrefixWriter,
    NodeToolsError,
    load_node_tools_bundle,
    resolve_node_tools_dir,
    smoke_test_node_tools,
    stage_node_tools_tree,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked

AWS_CREDENTIAL_ENV_VARS = (
    "AWS_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
)


def install_locked_node_tools(repo_root: Path, tool_dir: Path) -> Path:
    """Install the committed OpenClaw/ACPX lock into *tool_dir* and return its bin directory."""
    try:
        bundle = load_node_tools_bundle(resolve_node_tools_dir(repo_root))
        stage_node_tools_tree(bundle, tool_dir, LocalPrefixWriter())
        bin_dir = tool_dir / "node_modules" / ".bin"
        smoke_test_node_tools(bin_dir, bundle)
    except NodeToolsError as exc:
        raise CiWorkflowError(f"locked node-tools install failed: {exc}") from exc
    return bin_dir


def run_vendored_host_checks(repo_root: Path) -> None:
    """Run the vendored memory plugin host-functional verification."""
    resolved_repo_root = repo_root.expanduser().resolve()
    tool_dir = Path(tempfile.mkdtemp(prefix="strongclaw-openclaw-cli."))
    try:
        bin_dir = install_locked_node_tools(resolved_repo_root, tool_dir)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
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
