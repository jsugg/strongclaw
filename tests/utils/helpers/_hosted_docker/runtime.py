"""Hosted macOS runtime installation helpers."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context, load_report, write_report
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_github_env, write_json
from tests.utils.helpers._hosted_docker.models import RuntimeInstallReport
from tests.utils.helpers._hosted_docker.shell import (
    macos_env,
    run_checked,
    sysctl_int,
    wait_for_docker_ready,
)


def wait_runtime_ready(
    context_path: Path, *, github_env_file: Path | None = None
) -> RuntimeInstallReport:
    """Verify the OrbStack Docker runtime is ready and write the install report.

    Call this after the OrbStack setup action has run. The function runs a short
    connectivity check and writes the :class:`RuntimeInstallReport` for the scenario.
    """
    context = load_context(context_path)
    if context.platform != "macos":
        raise FreshHostError(
            "hosted docker runtime installation is only supported for macOS contexts"
        )

    repo_root = Path(context.repo_root).resolve()
    report_path = Path(context.runtime_report_path or "").resolve()
    runtime_provider = context.runtime_provider or "orbstack"
    arch = os.uname().machine
    host_cpu_count = sysctl_int("hw.ncpu")
    host_memory_bytes = sysctl_int("hw.memsize")
    host_memory_gib = (
        max(1, host_memory_bytes // 1073741824) if host_memory_bytes is not None else None
    )
    docker_config = os.environ.get("DOCKER_CONFIG", str((Path.home() / ".docker").resolve()))
    docker_host = os.environ.get("DOCKER_HOST", "")
    env = macos_env()
    env["DOCKER_CONFIG"] = docker_config
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    failure_reason: str | None = None
    started_at = now_iso()
    started = time.monotonic()

    log(f"wait_runtime_ready: DOCKER_HOST={docker_host!r} DOCKER_CONFIG={docker_config!r}")

    try:
        # OrbStack is ready immediately — short poll to confirm socket is live
        wait_for_docker_ready(cwd=repo_root, env=env, max_attempts=20)
        for command in (
            ["docker", "version"],
            ["docker", "compose", "version"],
            ["docker", "info"],
        ):
            run_checked(command, cwd=repo_root, env=env, timeout_seconds=120)
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)

    finished_at = now_iso()
    duration_seconds = round(time.monotonic() - started, 3)

    report = RuntimeInstallReport(
        runtime_provider=runtime_provider,
        arch=arch,
        host_cpu_count=host_cpu_count,
        host_memory_gib=host_memory_gib,
        colima_cpu_count=None,
        colima_memory_gib=None,
        docker_host=docker_host or None,
        docker_config=docker_config,
        installed_tools=["orbstack", "docker", "docker-compose"],
        failure_reason=failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        created_at=finished_at,
    )
    write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if failure_reason is not None:
        main_report.failure_reason = failure_reason
        main_report.status = "failure"
    write_report(main_report, Path(context.report_path).resolve())
    write_github_env(
        {
            "FRESH_HOST_HOST_CPU_COUNT": str(host_cpu_count or ""),
            "FRESH_HOST_HOST_MEMORY_GIB": str(host_memory_gib or ""),
        },
        github_env_file,
    )
    if failure_reason is not None:
        raise FreshHostError(failure_reason)
    return report
