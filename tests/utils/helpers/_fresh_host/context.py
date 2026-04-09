"""Context preparation for fresh-host CI scenarios."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from tests.utils.helpers._fresh_host.models import (
    DEFAULT_DOCKER_PULL_MAX_ATTEMPTS,
    DEFAULT_DOCKER_PULL_PARALLELISM,
    FreshHostContext,
    FreshHostReport,
)
from tests.utils.helpers._fresh_host.storage import (
    load_context,
    log,
    now_iso,
    require_scenario,
    write_github_env,
    write_json,
    write_report,
)


def _selected_runtime_provider() -> str | None:
    """Resolve the selected runtime provider from workflow environment."""
    return (
        os.environ.get("FRESH_HOST_RUNTIME_PROVIDER", "").strip()
        or os.environ.get("DEFAULT_MACOS_RUNTIME_PROVIDER", "").strip()
        or None
    )


def _selected_pull_parallelism() -> int:
    """Resolve configured Docker pull parallelism."""
    return int(
        os.environ.get(
            "FRESH_HOST_SELECTED_DOCKER_PULL_PARALLELISM",
            os.environ.get(
                "FRESH_HOST_DOCKER_PULL_PARALLELISM",
                str(DEFAULT_DOCKER_PULL_PARALLELISM),
            ),
        )
    )


def _selected_pull_max_attempts() -> int:
    """Resolve configured Docker pull retry count."""
    return int(
        os.environ.get(
            "FRESH_HOST_SELECTED_DOCKER_PULL_MAX_ATTEMPTS",
            os.environ.get(
                "FRESH_HOST_DOCKER_PULL_MAX_ATTEMPTS",
                str(DEFAULT_DOCKER_PULL_MAX_ATTEMPTS),
            ),
        )
    )


def _ensure_cache_dirs() -> None:
    """Create configured workflow cache roots when present."""
    for env_name in (
        "FRESH_HOST_CACHE_ROOT",
        "UV_CACHE_DIR",
        "npm_config_cache",
    ):
        raw_path = os.environ.get(env_name, "").strip()
        if raw_path:
            Path(raw_path).expanduser().resolve().mkdir(parents=True, exist_ok=True)


def prepare_context(
    *,
    scenario_id: str,
    profile: str = "openclaw-default",
    repo_root: Path,
    runner_temp: Path,
    workspace: Path,
    github_env_file: Path | None,
) -> FreshHostContext:
    """Create and persist one fresh-host execution context."""
    spec = require_scenario(scenario_id)
    tmp_root = runner_temp / f"strongclaw-{spec.platform}-host"
    report_dir = runner_temp / "fresh-host-reports" / scenario_id
    context_dir = runner_temp / "fresh-host" / scenario_id
    app_home = tmp_root / "home"
    xdg_runtime_dir = tmp_root / "xdg-runtime" if spec.platform == "linux" else None
    diagnostics_dir = report_dir / "diagnostics"
    context_file = context_dir / "context.json"
    report_file = report_dir / "report.json"
    runtime_report = (
        report_dir / "runtime-install-report.json" if spec.platform == "macos" else None
    )
    image_report = report_dir / "image-ensure-report.json" if spec.ensure_images else None

    context = FreshHostContext(
        version=1,
        scenario_id=spec.scenario_id,
        platform=spec.platform,
        job_name=spec.job_name,
        repo_root=str(repo_root.resolve()),
        workspace=str(workspace.resolve()),
        runner_temp=str(runner_temp.resolve()),
        tmp_root=str(tmp_root.resolve()),
        app_home=str(app_home.resolve()),
        xdg_runtime_dir=str(xdg_runtime_dir.resolve()) if xdg_runtime_dir is not None else None,
        report_dir=str(report_dir.resolve()),
        report_path=str(report_file.resolve()),
        context_path=str(context_file.resolve()),
        runtime_report_path=str(runtime_report.resolve()) if runtime_report is not None else None,
        image_report_path=str(image_report.resolve()) if image_report is not None else None,
        diagnostics_dir=str(diagnostics_dir.resolve()),
        profile=profile,
        runtime_provider="docker" if spec.platform == "linux" else _selected_runtime_provider(),
        docker_pull_parallelism=_selected_pull_parallelism(),
        docker_pull_max_attempts=_selected_pull_max_attempts(),
        compose_variant=spec.compose_variant,
        activate_services=spec.activate_services,
        ensure_images=spec.ensure_images,
        normalize_machine_name=spec.normalize_machine_name,
        verify_launchd=spec.verify_launchd,
        verify_rendered_files=spec.verify_rendered_files,
        exercise_sidecars=spec.exercise_sidecars,
        exercise_browser_lab=spec.exercise_browser_lab,
        phase_names=list(spec.phase_names),
        compose_files=[
            str((repo_root / relative_path).resolve()) for relative_path in spec.compose_files
        ],
    )
    report = FreshHostReport(
        scenario_id=context.scenario_id,
        job_name=context.job_name,
        platform=context.platform,
        runtime_provider=context.runtime_provider,
        phases=[],
        diagnostics_dir=context.diagnostics_dir,
        runtime_report_path=context.runtime_report_path,
        image_report_path=context.image_report_path,
        failure_reason=None,
        status="pending",
        created_at=now_iso(),
        updated_at=now_iso(),
    )

    _ensure_cache_dirs()
    write_json(asdict(context), Path(context.context_path))
    write_report(report, Path(context.report_path))

    exports = {
        "FRESH_HOST_CONTEXT": context.context_path,
        "FRESH_HOST_REPORT_DIR": context.report_dir,
        "FRESH_HOST_REPORT_JSON": context.report_path,
        "TMP_ROOT": context.tmp_root,
        "STRONGCLAW_APP_HOME": context.app_home,
        "FRESH_HOST_PROFILE": context.profile,
        "FRESH_HOST_SELECTED_DOCKER_PULL_PARALLELISM": str(context.docker_pull_parallelism),
        "FRESH_HOST_SELECTED_DOCKER_PULL_MAX_ATTEMPTS": str(context.docker_pull_max_attempts),
    }
    if context.xdg_runtime_dir is not None:
        exports["STRONGCLAW_XDG_RUNTIME_DIR"] = context.xdg_runtime_dir
    if context.runtime_provider is not None:
        exports["FRESH_HOST_RUNTIME_PROVIDER"] = context.runtime_provider
    if context.compose_variant is not None:
        exports["STRONGCLAW_COMPOSE_VARIANT"] = context.compose_variant
    if context.runtime_report_path is not None:
        exports["FRESH_HOST_RUNTIME_REPORT_JSON"] = context.runtime_report_path
    if context.image_report_path is not None:
        exports["FRESH_HOST_IMAGE_REPORT_JSON"] = context.image_report_path
    if len(context.compose_files) == 1:
        exports["FRESH_HOST_PRIMARY_COMPOSE_FILE"] = context.compose_files[0]
    write_github_env(exports, github_env_file)
    log(f"Prepared context for scenario={context.scenario_id} at {context.context_path}.")
    return context


def _display_path(path: Path, *, repo_root: Path) -> str:
    """Return one display path, relative to repo root when possible."""
    resolved_path = path.expanduser().resolve()
    try:
        return str(resolved_path.relative_to(repo_root))
    except ValueError:
        return str(resolved_path)


def preview_context(
    context_file: Path,
    *,
    summary_file: Path | None = None,
) -> dict[str, object]:
    """Write one human-readable context preview for the prepared scenario."""
    context = load_context(context_file.expanduser().resolve())
    repo_root = Path(context.repo_root).expanduser().resolve()
    report_dir = Path(context.report_dir).expanduser().resolve()
    preview_path = report_dir / "context-preview.json"

    compose_files = [
        _display_path(Path(compose_path), repo_root=repo_root)
        for compose_path in context.compose_files
    ]
    payload: dict[str, object] = {
        "scenario_id": context.scenario_id,
        "platform": context.platform,
        "job_name": context.job_name,
        "runtime_provider": context.runtime_provider,
        "profile": context.profile,
        "docker_pull_parallelism": context.docker_pull_parallelism,
        "docker_pull_max_attempts": context.docker_pull_max_attempts,
        "activate_services": context.activate_services,
        "ensure_images": context.ensure_images,
        "phase_names": list(context.phase_names),
        "compose_files": compose_files,
        "context_path": context.context_path,
        "report_path": context.report_path,
        "report_dir": context.report_dir,
        "diagnostics_dir": context.diagnostics_dir,
        "preview_path": str(preview_path),
        "created_at": now_iso(),
    }
    write_json(payload, preview_path)

    if summary_file is not None:
        summary_lines = [
            "### Fresh-Host Context Preview",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Scenario | {context.scenario_id} |",
            f"| Platform | {context.platform} |",
            f"| Job | {context.job_name} |",
            f"| Runtime provider | {context.runtime_provider or 'n/a'} |",
            f"| Profile | {context.profile} |",
            f"| Ensure images | {context.ensure_images} |",
            f"| Activate services in setup | {context.activate_services} |",
            f"| Docker pull parallelism | {context.docker_pull_parallelism} |",
            f"| Docker pull max attempts | {context.docker_pull_max_attempts} |",
            f"| Context JSON | `{context.context_path}` |",
            f"| Report JSON | `{context.report_path}` |",
            f"| Diagnostics dir | `{context.diagnostics_dir}` |",
            f"| Preview JSON | `{preview_path}` |",
            "",
            "Planned phases:",
            "",
        ]
        summary_lines.extend(f"- `{phase_name}`" for phase_name in context.phase_names)
        summary_lines.extend(["", "Compose files:", ""])
        summary_lines.extend(f"- `{compose_file}`" for compose_file in compose_files)
        summary_lines.append("")
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        with summary_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary_lines))

    log(f"Context preview written to {preview_path}.")
    return payload
