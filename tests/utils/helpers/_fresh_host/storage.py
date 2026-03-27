"""JSON persistence and formatting helpers for fresh-host reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from clawops.typed_values import (
    as_bool,
    as_int,
    as_mapping,
    as_optional_string,
    as_string,
    as_string_list,
)
from tests.utils.helpers._fresh_host.models import (
    LOG_PREFIX,
    SCENARIO_SPECS,
    FreshHostContext,
    FreshHostError,
    FreshHostReport,
    PhaseResult,
    PhaseStatus,
    PlatformName,
    ScenarioId,
    ScenarioSpec,
)


def log(message: str) -> None:
    """Emit one CI-friendly log line."""
    print(f"{LOG_PREFIX} {message}", flush=True)


def now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC).isoformat()


def require_scenario(scenario_id: str) -> ScenarioSpec:
    """Resolve one known scenario spec."""
    try:
        return SCENARIO_SPECS[scenario_id]  # type: ignore[index]
    except KeyError as exc:
        supported = ", ".join(sorted(SCENARIO_SPECS))
        raise FreshHostError(
            f"Unsupported scenario '{scenario_id}'. Expected one of: {supported}."
        ) from exc


def repo_root(path_text: str) -> Path:
    """Resolve the repository root path."""
    return Path(path_text).expanduser().resolve()


def context_path(path_text: str) -> Path:
    """Resolve one context/report path."""
    return Path(path_text).expanduser().resolve()


def write_json(payload: object, path: Path) -> None:
    """Write one JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    """Read one JSON payload."""
    if not path.is_file():
        raise FreshHostError(f"expected JSON file but found {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(as_mapping(payload, path=str(path)))


def write_github_env(assignments: dict[str, str], github_env_file: Path | None) -> None:
    """Append environment exports for downstream workflow steps."""
    if github_env_file is None:
        return
    lines = [f"{key}={value}" for key, value in assignments.items()]
    with github_env_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def format_duration(seconds: float) -> str:
    """Render one duration in minutes and seconds."""
    total = int(round(seconds))
    minutes, remaining = divmod(total, 60)
    return f"{minutes}m {remaining}s"


def _scenario_id(value: object, *, path: str) -> ScenarioId:
    scenario_id = as_string(value, path=path)
    if scenario_id not in SCENARIO_SPECS:
        raise FreshHostError(f"{path} must be one of: {', '.join(sorted(SCENARIO_SPECS))}")
    return require_scenario(scenario_id).scenario_id


def _platform_name(value: object, *, path: str) -> PlatformName:
    platform = as_string(value, path=path)
    if platform not in {"linux", "macos"}:
        raise FreshHostError(f"{path} must be 'linux' or 'macos'")
    return cast(PlatformName, platform)


def _phase_status(value: object, *, path: str) -> PhaseStatus:
    status = as_string(value, path=path)
    if status not in {"success", "failure", "skipped"}:
        raise FreshHostError(f"{path} must be one of: success, failure, skipped")
    return cast(PhaseStatus, status)


def _duration_seconds(value: object, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FreshHostError(f"{path} must be numeric")
    return float(value)


def load_context(path: Path) -> FreshHostContext:
    """Load one scenario context from disk."""
    payload = read_json(path)
    return FreshHostContext(
        version=as_int(payload.get("version"), path="context.version"),
        scenario_id=_scenario_id(payload.get("scenario_id"), path="context.scenario_id"),
        platform=_platform_name(payload.get("platform"), path="context.platform"),
        job_name=as_string(payload.get("job_name"), path="context.job_name"),
        repo_root=as_string(payload.get("repo_root"), path="context.repo_root"),
        workspace=as_string(payload.get("workspace"), path="context.workspace"),
        runner_temp=as_string(payload.get("runner_temp"), path="context.runner_temp"),
        tmp_root=as_string(payload.get("tmp_root"), path="context.tmp_root"),
        app_home=as_string(payload.get("app_home"), path="context.app_home"),
        xdg_runtime_dir=as_optional_string(
            payload.get("xdg_runtime_dir"),
            path="context.xdg_runtime_dir",
        ),
        report_dir=as_string(payload.get("report_dir"), path="context.report_dir"),
        report_path=as_string(payload.get("report_path"), path="context.report_path"),
        context_path=as_string(payload.get("context_path"), path="context.context_path"),
        runtime_report_path=as_optional_string(
            payload.get("runtime_report_path"),
            path="context.runtime_report_path",
        ),
        image_report_path=as_optional_string(
            payload.get("image_report_path"),
            path="context.image_report_path",
        ),
        diagnostics_dir=as_string(payload.get("diagnostics_dir"), path="context.diagnostics_dir"),
        runtime_provider=as_optional_string(
            payload.get("runtime_provider"),
            path="context.runtime_provider",
        ),
        docker_pull_parallelism=as_int(
            payload.get("docker_pull_parallelism"),
            path="context.docker_pull_parallelism",
        ),
        docker_pull_max_attempts=as_int(
            payload.get("docker_pull_max_attempts"),
            path="context.docker_pull_max_attempts",
        ),
        compose_variant=as_optional_string(
            payload.get("compose_variant"),
            path="context.compose_variant",
        ),
        activate_services=as_bool(
            payload.get("activate_services"),
            path="context.activate_services",
        ),
        ensure_images=as_bool(payload.get("ensure_images"), path="context.ensure_images"),
        normalize_machine_name=as_bool(
            payload.get("normalize_machine_name"),
            path="context.normalize_machine_name",
        ),
        verify_launchd=as_bool(payload.get("verify_launchd"), path="context.verify_launchd"),
        verify_rendered_files=as_bool(
            payload.get("verify_rendered_files"),
            path="context.verify_rendered_files",
        ),
        exercise_sidecars=as_bool(
            payload.get("exercise_sidecars"),
            path="context.exercise_sidecars",
        ),
        exercise_browser_lab=as_bool(
            payload.get("exercise_browser_lab"),
            path="context.exercise_browser_lab",
        ),
        phase_names=list(as_string_list(payload.get("phase_names"), path="context.phase_names")),
        compose_files=list(
            as_string_list(payload.get("compose_files"), path="context.compose_files")
        ),
    )


def load_report(path: Path) -> FreshHostReport:
    """Load one scenario report from disk."""
    payload = read_json(path)
    raw_phases = payload.get("phases", [])
    if not isinstance(raw_phases, list):
        raise FreshHostError("report.phases must be a list")
    phase_payloads = cast(list[object], raw_phases)
    phases = [
        PhaseResult(
            name=as_string(raw_phase_mapping.get("name"), path="report.phases[].name"),
            status=_phase_status(raw_phase_mapping.get("status"), path="report.phases[].status"),
            duration_seconds=_duration_seconds(
                raw_phase_mapping.get("duration_seconds"),
                path="report.phases[].duration_seconds",
            ),
            started_at=as_string(
                raw_phase_mapping.get("started_at"),
                path="report.phases[].started_at",
            ),
            finished_at=as_string(
                raw_phase_mapping.get("finished_at"),
                path="report.phases[].finished_at",
            ),
            command=(
                list(
                    as_string_list(raw_phase_mapping.get("command"), path="report.phases[].command")
                )
                if raw_phase_mapping.get("command") is not None
                else None
            ),
            failure_reason=as_optional_string(
                raw_phase_mapping.get("failure_reason"),
                path="report.phases[].failure_reason",
            ),
            notes=list(
                as_string_list(raw_phase_mapping.get("notes", []), path="report.phases[].notes")
            ),
        )
        for raw_phase_mapping in (
            as_mapping(raw_phase, path="report.phases[]") for raw_phase in phase_payloads
        )
    ]
    return FreshHostReport(
        scenario_id=as_string(payload.get("scenario_id"), path="report.scenario_id"),
        job_name=as_string(payload.get("job_name"), path="report.job_name"),
        platform=as_string(payload.get("platform"), path="report.platform"),
        runtime_provider=as_optional_string(
            payload.get("runtime_provider"),
            path="report.runtime_provider",
        ),
        phases=phases,
        diagnostics_dir=as_string(payload.get("diagnostics_dir"), path="report.diagnostics_dir"),
        runtime_report_path=as_optional_string(
            payload.get("runtime_report_path"),
            path="report.runtime_report_path",
        ),
        image_report_path=as_optional_string(
            payload.get("image_report_path"),
            path="report.image_report_path",
        ),
        failure_reason=as_optional_string(
            payload.get("failure_reason"),
            path="report.failure_reason",
        ),
        status=as_string(payload.get("status"), path="report.status"),
        created_at=as_string(payload.get("created_at"), path="report.created_at"),
        updated_at=as_string(payload.get("updated_at"), path="report.updated_at"),
    )


def write_report(report: FreshHostReport, path: Path) -> None:
    """Persist one scenario report to disk."""
    report.updated_at = now_iso()
    write_json(asdict(report), path)
