"""Diagnostics, cleanup, and summary helpers for fresh-host CI."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import cast

from tests.utils.helpers._fresh_host.macos import cleanup_macos
from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostReport, PhaseResult
from tests.utils.helpers._fresh_host.shell import capture_to_file, phase_env
from tests.utils.helpers._fresh_host.storage import (
    context_path,
    format_duration,
    load_context,
    load_report,
    log,
    now_iso,
    read_json,
    repo_root,
    write_report,
)


def _diagnostic_commands(context: FreshHostContext) -> dict[Path, list[str]]:
    """Build the diagnostic command plan for a scenario."""
    diagnostics_dir = context_path(context.diagnostics_dir)
    commands = {
        diagnostics_dir / "docker-info.txt": ["docker", "info"],
        diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
        diagnostics_dir / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
    }
    if context.platform == "linux":
        return commands

    commands[diagnostics_dir / "launchctl-gateway.txt"] = [
        "launchctl",
        "print",
        f"gui/{os.getuid()}/ai.openclaw.gateway",
    ]
    commands[diagnostics_dir / "launchctl-sidecars.txt"] = [
        "launchctl",
        "print",
        f"gui/{os.getuid()}/ai.openclaw.sidecars",
    ]
    commands[diagnostics_dir / "docker-ps.txt"] = ["docker", "ps", "-a"]
    if context.compose_files:
        primary_compose_file = context.compose_files[0]
        commands[diagnostics_dir / "compose-ps.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "ps",
        ]
        commands[diagnostics_dir / "compose-logs.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "logs",
            "--no-color",
        ]
    if shutil.which("colima") is not None:
        commands[diagnostics_dir / "colima-status.txt"] = ["colima", "status"]
        commands[diagnostics_dir / "colima-list.txt"] = ["colima", "list"]
    return commands


def _list_length(payload: dict[str, object], key: str) -> int:
    """Return the length of one JSON list field when present."""
    value = payload.get(key)
    return len(cast(list[object], value)) if isinstance(value, list) else 0


def collect_diagnostics(context_file: Path) -> FreshHostReport:
    """Collect best-effort diagnostics for the active scenario."""
    context = load_context(context_file)
    report_file = context_path(context.report_path)
    report = load_report(report_file)
    started = time.monotonic()
    notes: list[str] = []
    diagnostics_dir = context_path(context.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    for output_path, command in _diagnostic_commands(context).items():
        note = capture_to_file(
            command,
            output_path=output_path,
            cwd=repo_root(context.repo_root),
            env=phase_env(context),
        )
        if note is not None:
            notes.append(note)
            log(note)
    if context.platform == "macos":
        logs_root = context_path(context.app_home) / ".openclaw" / "logs"
        for log_name in (
            "launchd-gateway.out.log",
            "launchd-gateway.err.log",
            "launchd-sidecars.out.log",
            "launchd-sidecars.err.log",
        ):
            source_path = logs_root / log_name
            if source_path.is_file():
                shutil.copyfile(source_path, diagnostics_dir / log_name)
    report.phases.append(
        PhaseResult(
            name="collect-diagnostics",
            status="success",
            duration_seconds=round(time.monotonic() - started, 3),
            started_at=now_iso(),
            finished_at=now_iso(),
            command=None,
            notes=notes,
        )
    )
    write_report(report, report_file)
    return report


def cleanup(context_file: Path) -> FreshHostReport:
    """Run best-effort scenario cleanup."""
    context = load_context(context_file)
    report_file = context_path(context.report_path)
    report = load_report(report_file)
    started = time.monotonic()
    command = cleanup_macos(context) if context.platform == "macos" else None
    report.phases.append(
        PhaseResult(
            name="cleanup",
            status="success",
            duration_seconds=round(time.monotonic() - started, 3),
            started_at=now_iso(),
            finished_at=now_iso(),
            command=command,
            notes=[],
        )
    )
    write_report(report, report_file)
    return report


def _append_phase_table(lines: list[str], report: FreshHostReport) -> None:
    """Append the per-phase summary table."""
    if not report.phases:
        lines.extend(["No phase timings were recorded.", ""])
        return
    lines.extend(["| Phase | Status | Duration |", "| --- | --- | --- |"])
    for phase in report.phases:
        lines.append(
            f"| {phase.name} | {phase.status} | {format_duration(phase.duration_seconds)} |"
        )
    total_seconds = sum(phase.duration_seconds for phase in report.phases)
    lines.extend(["", f"Known phase total: {format_duration(total_seconds)}", ""])


def _append_child_report_sections(lines: list[str], report: FreshHostReport) -> None:
    """Append runtime/image child report summaries when present."""
    if report.image_report_path is not None and context_path(report.image_report_path).is_file():
        image_report = read_json(context_path(report.image_report_path))
        lines.extend(
            [
                "| Image ensure field | Value |",
                "| --- | --- |",
                f"| Images requested | {_list_length(image_report, 'images')} |",
                f"| Missing before pull | {_list_length(image_report, 'missing_before_pull')} |",
                f"| Pull attempts | {image_report.get('pull_attempt_count')} |",
                f"| Retried images | {_list_length(image_report, 'retried_images')} |",
                f"| Pulled images | {_list_length(image_report, 'pulled_images')} |",
                f"| Failure reason | {image_report.get('failure_reason')} |",
                "",
            ]
        )
    if (
        report.runtime_report_path is not None
        and context_path(report.runtime_report_path).is_file()
    ):
        runtime_report = read_json(context_path(report.runtime_report_path))
        if runtime_report.get("host_cpu_count") and runtime_report.get("host_memory_gib"):
            lines.append(
                f"Host resources: {runtime_report['host_cpu_count']} CPU / {runtime_report['host_memory_gib']} GiB"
            )
        if runtime_report.get("colima_cpu_count") and runtime_report.get("colima_memory_gib"):
            lines.append(
                f"Colima resources: {runtime_report['colima_cpu_count']} CPU / {runtime_report['colima_memory_gib']} GiB"
            )
        if lines[-1] != "":
            lines.append("")


def write_summary(context_file: Path, summary_file: Path) -> None:
    """Render one GitHub step summary for the scenario report."""
    context = load_context(context_file)
    report = load_report(context_path(context.report_path))
    lines = [
        f"## {report.job_name}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Scenario | {report.scenario_id} |",
        f"| Platform | {report.platform} |",
        f"| Status | {report.status} |",
        f"| Runtime provider | {report.runtime_provider or 'n/a'} |",
        f"| Activate services in setup | {context.activate_services} |",
        f"| Docker pull parallelism | {context.docker_pull_parallelism} |",
        f"| Docker pull max attempts | {context.docker_pull_max_attempts} |",
    ]
    for label, env_name in (
        ("Package cache", "FRESH_HOST_PACKAGE_CACHE_ENABLED"),
        ("Package cache hit", "FRESH_HOST_PACKAGE_CACHE_HIT"),
        ("Homebrew cache", "FRESH_HOST_HOMEBREW_CACHE_ENABLED"),
        ("Homebrew cache hit", "FRESH_HOST_HOMEBREW_CACHE_HIT"),
        ("Runtime download cache", "FRESH_HOST_RUNTIME_DOWNLOAD_CACHE_ENABLED"),
        ("Runtime download cache hit", "FRESH_HOST_RUNTIME_DOWNLOAD_CACHE_HIT"),
    ):
        raw_value = os.environ.get(env_name)
        if raw_value is not None:
            lines.append(f"| {label} | {str(raw_value).lower()} |")
    lines.append("")
    _append_phase_table(lines, report)
    _append_child_report_sections(lines, report)
    lines.append(f"Diagnostics artifact root: `{report.diagnostics_dir}`")
    lines.append("")
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
