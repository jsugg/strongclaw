"""Diagnostics, cleanup, and summary helpers for fresh-host CI."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from tests.utils.helpers._fresh_host.macos import cleanup_macos
from tests.utils.helpers._fresh_host.models import (
    FreshHostContext,
    FreshHostError,
    FreshHostReport,
    PhaseResult,
)
from tests.utils.helpers._fresh_host.shell import capture_to_file, compose_probe_env, phase_env
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

KPI_EVIDENCE_SCHEMA_VERSION = "1.0.0"


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


def _diagnostic_env(
    context: FreshHostContext,
    *,
    command: list[str],
) -> dict[str, str]:
    """Return the environment for one diagnostic command."""
    if len(command) >= 4 and command[:3] == ["docker", "compose", "-f"]:
        return compose_probe_env(
            context,
            compose_file=Path(command[3]),
            repo_local_state=context.platform == "macos",
        )
    return phase_env(context)


def _list_length(payload: dict[str, object], key: str) -> int:
    """Return the length of one JSON list field when present."""
    value = payload.get(key)
    return len(cast(list[object], value)) if isinstance(value, list) else 0


def _load_child_report(path_text: str | None) -> dict[str, object] | None:
    """Load one optional child report payload."""
    if path_text is None:
        return None
    path = context_path(path_text)
    if not path.is_file():
        return None
    return read_json(path)


def _duration_seconds(payload: dict[str, object] | None) -> float | None:
    """Extract one duration field from a child report when available."""
    if payload is None:
        return None
    raw_seconds = payload.get("duration_seconds")
    if isinstance(raw_seconds, bool) or not isinstance(raw_seconds, (int, float)):
        return None
    seconds = float(raw_seconds)
    return seconds if seconds >= 0 else None


def _report_window_seconds(report: FreshHostReport) -> float | None:
    """Compute the report timeline duration from report metadata."""
    try:
        created_at = datetime.fromisoformat(report.created_at)
        updated_at = datetime.fromisoformat(report.updated_at)
    except ValueError:
        return None
    return max((updated_at - created_at).total_seconds(), 0.0)


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
            env=_diagnostic_env(context, command=command),
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
    """Run scenario cleanup and persist the structured result."""
    context = load_context(context_file)
    report_file = context_path(context.report_path)
    report = load_report(report_file)
    started_at = now_iso()
    started = time.monotonic()
    command: list[str] | None = None
    notes: list[str] = []
    try:
        if context.platform == "macos":
            result = cleanup_macos(context)
            command = result.command
            notes = result.notes
    except Exception as exc:  # noqa: BLE001
        report.phases.append(
            PhaseResult(
                name="cleanup",
                status="failure",
                duration_seconds=round(time.monotonic() - started, 3),
                started_at=started_at,
                finished_at=now_iso(),
                command=command,
                failure_reason=str(exc),
                notes=notes,
            )
        )
        report.failure_reason = str(exc)
        report.status = "failure"
        write_report(report, report_file)
        raise FreshHostError(str(exc)) from exc
    report.phases.append(
        PhaseResult(
            name="cleanup",
            status="success",
            duration_seconds=round(time.monotonic() - started, 3),
            started_at=started_at,
            finished_at=now_iso(),
            command=command,
            notes=notes,
        )
    )
    write_report(report, report_file)
    return report


def _append_phase_table(lines: list[str], report: FreshHostReport) -> float:
    """Append the per-phase summary table."""
    if not report.phases:
        lines.extend(["No phase timings were recorded.", ""])
        return 0.0
    lines.extend(["| Phase | Status | Duration |", "| --- | --- | --- |"])
    for phase in report.phases:
        lines.append(
            f"| {phase.name} | {phase.status} | {format_duration(phase.duration_seconds)} |"
        )
    total_seconds = sum(phase.duration_seconds for phase in report.phases)
    lines.extend(
        ["", f"Scenario phase total (fresh-host phases only): {format_duration(total_seconds)}", ""]
    )
    return total_seconds


def _append_timing_kpis(
    lines: list[str],
    *,
    report: FreshHostReport,
    scenario_phase_seconds: float,
    runtime_report: dict[str, object] | None,
    image_report: dict[str, object] | None,
) -> None:
    """Append high-level timing KPIs for CI triage."""
    runtime_install_seconds = _duration_seconds(runtime_report)
    image_ensure_seconds = _duration_seconds(image_report)
    tracked_execution_seconds = (
        scenario_phase_seconds + (runtime_install_seconds or 0.0) + (image_ensure_seconds or 0.0)
    )
    report_window_seconds = _report_window_seconds(report)
    unattributed_seconds = (
        max(report_window_seconds - tracked_execution_seconds, 0.0)
        if report_window_seconds is not None
        else None
    )
    lines.extend(
        [
            "| Timing KPI | Value |",
            "| --- | --- |",
            (
                "| Scenario phase total (fresh-host phases only) | "
                f"{format_duration(scenario_phase_seconds)} |"
            ),
            (
                "| Hosted runtime install | "
                f"{format_duration(runtime_install_seconds) if runtime_install_seconds is not None else 'n/a'} |"
            ),
            (
                "| Hosted image ensure | "
                f"{format_duration(image_ensure_seconds) if image_ensure_seconds is not None else 'n/a'} |"
            ),
            f"| Tracked execution total | {format_duration(tracked_execution_seconds)} |",
            (
                "| Execution window (report timeline) | "
                f"{format_duration(report_window_seconds) if report_window_seconds is not None else 'n/a'} |"
            ),
            (
                "| Unattributed execution | "
                f"{format_duration(unattributed_seconds) if unattributed_seconds is not None else 'n/a'} |"
            ),
            "",
        ]
    )


def _kpi_evidence_path(context: FreshHostContext) -> Path:
    """Resolve where to persist machine-readable KPI evidence."""
    configured = os.environ.get("FRESH_HOST_KPI_EVIDENCE_FILE", "").strip()
    if configured:
        return context_path(configured)
    evidence_name = f"ci-macos-kpi-{context.scenario_id}.json"
    return repo_root(context.repo_root) / ".omx" / "evidence" / evidence_name


def _write_kpi_evidence(
    *,
    context: FreshHostContext,
    report: FreshHostReport,
    scenario_phase_seconds: float,
    runtime_report: dict[str, object] | None,
    image_report: dict[str, object] | None,
) -> Path:
    """Persist machine-readable KPI evidence for downstream comparison tooling."""
    runtime_install_seconds = _duration_seconds(runtime_report)
    image_ensure_seconds = _duration_seconds(image_report)
    tracked_execution_seconds = (
        scenario_phase_seconds + (runtime_install_seconds or 0.0) + (image_ensure_seconds or 0.0)
    )
    report_window_seconds = _report_window_seconds(report)
    unattributed_seconds = (
        max(report_window_seconds - tracked_execution_seconds, 0.0)
        if report_window_seconds is not None
        else None
    )
    payload = {
        "schema_version": KPI_EVIDENCE_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "run": {
            "id": os.environ.get("GITHUB_RUN_ID"),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "ref": os.environ.get("GITHUB_REF"),
            "sha": os.environ.get("GITHUB_SHA"),
        },
        "scenario": {
            "id": report.scenario_id,
            "job_name": report.job_name,
            "platform": report.platform,
            "runtime_provider": report.runtime_provider,
            "freshness_mode": context.freshness_mode,
        },
        "timings_seconds": {
            "scenario_phase_total": round(scenario_phase_seconds, 3),
            "runtime_install": runtime_install_seconds,
            "image_ensure": image_ensure_seconds,
            "tracked_execution_total": round(tracked_execution_seconds, 3),
            "execution_window": report_window_seconds,
            "unattributed_execution": unattributed_seconds,
        },
        "status": report.status,
    }

    evidence_path = _kpi_evidence_path(context)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path


def _append_child_report_sections(
    lines: list[str],
    *,
    runtime_report: dict[str, object] | None,
    image_report: dict[str, object] | None,
) -> None:
    """Append runtime/image child report summaries when present."""
    if image_report is not None:
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
    if runtime_report is not None:
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
        f"| Freshness mode | {context.freshness_mode} |",
        f"| Activate services in setup | {context.activate_services} |",
        f"| Docker pull parallelism | {context.docker_pull_parallelism} |",
        f"| Docker pull max attempts | {context.docker_pull_max_attempts} |",
    ]
    for label, env_name in (
        ("Package cache", "FRESH_HOST_PACKAGE_CACHE_ENABLED"),
        ("Package cache hit", "FRESH_HOST_PACKAGE_CACHE_HIT"),
    ):
        raw_value = os.environ.get(env_name)
        if raw_value is not None:
            lines.append(f"| {label} | {str(raw_value).lower()} |")
    lines.append("")
    runtime_report = _load_child_report(report.runtime_report_path)
    image_report = _load_child_report(report.image_report_path)
    scenario_phase_seconds = _append_phase_table(lines, report)
    _append_timing_kpis(
        lines,
        report=report,
        scenario_phase_seconds=scenario_phase_seconds,
        runtime_report=runtime_report,
        image_report=image_report,
    )
    _append_child_report_sections(
        lines,
        runtime_report=runtime_report,
        image_report=image_report,
    )
    evidence_path = _write_kpi_evidence(
        context=context,
        report=report,
        scenario_phase_seconds=scenario_phase_seconds,
        runtime_report=runtime_report,
        image_report=image_report,
    )
    lines.append(f"KPI evidence JSON: `{evidence_path}`")
    lines.append("")
    lines.append(f"Diagnostics artifact root: `{report.diagnostics_dir}`")
    lines.append("")
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
