"""Scenario planning and execution for fresh-host CI."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from tests.utils.helpers._fresh_host import linux, macos
from tests.utils.helpers._fresh_host.models import (
    FreshHostContext,
    FreshHostError,
    FreshHostReport,
    PhaseResult,
)
from tests.utils.helpers._fresh_host.storage import (
    context_path,
    load_context,
    load_report,
    log,
    now_iso,
    write_report,
)


def scenario_phase_names(context: FreshHostContext) -> list[str]:
    """Return the ordered phase plan for one scenario context."""
    return list(context.phase_names)


def run_named_phase(context: FreshHostContext, phase_name: str) -> list[str] | None:
    """Execute one named scenario phase."""
    if phase_name == "bootstrap":
        return (
            linux.linux_bootstrap(context)
            if context.platform == "linux"
            else macos.macos_bootstrap(context)
        )
    if phase_name == "setup":
        return (
            linux.linux_setup(context)
            if context.platform == "linux"
            else macos.macos_setup(context)
        )
    if phase_name == "verify-rendered-files":
        if context.platform == "linux":
            linux.verify_linux_rendered_units(context)
        return None
    if phase_name == "verify-launchd":
        macos.verify_macos_launchd(context)
        return None
    if phase_name == "deactivate-services":
        if context.platform != "macos":
            raise FreshHostError("deactivate-services is only supported for macOS scenarios")
        return macos.deactivate_macos_host_services(context)
    if phase_name == "exercise-sidecars":
        return (
            linux.exercise_linux_sidecars(context)
            if context.platform == "linux"
            else macos.exercise_macos_sidecars(context)
        )
    if phase_name == "exercise-browser-lab":
        return (
            linux.exercise_linux_browser_lab(context)
            if context.platform == "linux"
            else macos.exercise_macos_browser_lab(context)
        )
    if phase_name == "normalize-machine-name":
        return macos.normalize_macos_machine_name(context)
    raise FreshHostError(f"Unsupported phase '{phase_name}'.")


def record_phase(
    *,
    report: FreshHostReport,
    report_path: Path,
    phase_name: str,
    action: Callable[[], list[str] | None],
) -> None:
    """Execute one phase and append the structured result."""
    started_at = now_iso()
    started = time.monotonic()
    command: list[str] | None = None
    try:
        command = action()
    except Exception as exc:  # noqa: BLE001
        report.phases.append(
            PhaseResult(
                name=phase_name,
                status="failure",
                duration_seconds=round(time.monotonic() - started, 3),
                started_at=started_at,
                finished_at=now_iso(),
                command=command,
                failure_reason=str(exc),
            )
        )
        report.failure_reason = str(exc)
        report.status = "failure"
        write_report(report, report_path)
        raise FreshHostError(str(exc)) from exc

    report.phases.append(
        PhaseResult(
            name=phase_name,
            status="success",
            duration_seconds=round(time.monotonic() - started, 3),
            started_at=started_at,
            finished_at=now_iso(),
            command=command,
        )
    )
    write_report(report, report_path)


def run_scenario(context_file: Path) -> FreshHostReport:
    """Run the configured phase plan for one scenario."""
    context = load_context(context_file)
    report_file = context_path(context.report_path)
    report = load_report(report_file)
    report.status = "running"
    write_report(report, report_file)
    for phase_name in scenario_phase_names(context):
        log(f"Starting phase={phase_name}.")
        record_phase(
            report=report,
            report_path=report_file,
            phase_name=phase_name,
            action=lambda phase_name=phase_name: run_named_phase(context, phase_name),
        )
    report.status = "success"
    write_report(report, report_file)
    return report
