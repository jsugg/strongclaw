"""Fresh-host CI orchestration helpers."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

ScenarioId = Literal["linux", "macos"]
PlatformName = Literal["linux", "macos"]
PhaseStatus = Literal["success", "failure", "skipped"]

LOG_PREFIX: Final[str] = "[fresh-host]"
DEFAULT_DOCKER_PULL_PARALLELISM: Final[int] = 2
DEFAULT_DOCKER_PULL_MAX_ATTEMPTS: Final[int] = 3


class FreshHostError(RuntimeError):
    """Raised when a fresh-host operation fails."""


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Static scenario configuration."""

    scenario_id: ScenarioId
    platform: PlatformName
    job_name: str
    compose_files: tuple[str, ...]
    normalize_machine_name: bool


@dataclass(slots=True)
class PhaseResult:
    """Structured record for one scenario phase."""

    name: str
    status: PhaseStatus
    duration_seconds: float
    started_at: str
    finished_at: str
    command: list[str] | None
    failure_reason: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FreshHostContext:
    """Serializable scenario context shared across workflow steps."""

    version: int
    scenario_id: ScenarioId
    platform: PlatformName
    job_name: str
    repo_root: str
    workspace: str
    runner_temp: str
    tmp_root: str
    app_home: str
    xdg_runtime_dir: str | None
    report_dir: str
    report_path: str
    context_path: str
    runtime_report_path: str | None
    image_report_path: str | None
    diagnostics_dir: str
    runtime_provider: str | None
    docker_pull_parallelism: int
    docker_pull_max_attempts: int
    activate_services: bool
    ensure_images: bool
    normalize_machine_name: bool
    verify_launchd: bool
    verify_rendered_files: bool
    exercise_sidecars: bool
    exercise_browser_lab: bool
    compose_files: list[str]


@dataclass(slots=True)
class FreshHostReport:
    """Structured report describing one fresh-host workflow run."""

    scenario_id: str
    job_name: str
    platform: str
    runtime_provider: str | None
    phases: list[PhaseResult]
    diagnostics_dir: str
    runtime_report_path: str | None
    image_report_path: str | None
    failure_reason: str | None
    status: str
    created_at: str
    updated_at: str


SCENARIO_SPECS: Final[dict[ScenarioId, ScenarioSpec]] = {
    "linux": ScenarioSpec(
        scenario_id="linux",
        platform="linux",
        job_name="Linux Fresh Host",
        compose_files=(
            "platform/compose/docker-compose.aux-stack.yaml",
            "platform/compose/docker-compose.browser-lab.yaml",
        ),
        normalize_machine_name=False,
    ),
    "macos": ScenarioSpec(
        scenario_id="macos",
        platform="macos",
        job_name="macOS Fresh Host",
        compose_files=(
            "platform/compose/docker-compose.aux-stack.yaml",
            "platform/compose/docker-compose.browser-lab.yaml",
        ),
        normalize_machine_name=True,
    ),
}


def _log(message: str) -> None:
    """Emit one CI-friendly log line."""
    print(f"{LOG_PREFIX} {message}", flush=True)


def _now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC).isoformat()


def _require_scenario(scenario_id: str) -> ScenarioSpec:
    """Resolve one known scenario spec."""
    try:
        return SCENARIO_SPECS[scenario_id]  # type: ignore[index]
    except KeyError as exc:
        supported = ", ".join(sorted(SCENARIO_SPECS))
        raise FreshHostError(
            f"Unsupported scenario '{scenario_id}'. Expected one of: {supported}."
        ) from exc


def _repo_root(path_text: str) -> Path:
    """Resolve the repository root path."""
    return Path(path_text).expanduser().resolve()


def _context_path(path_text: str) -> Path:
    """Resolve one context/report path."""
    return Path(path_text).expanduser().resolve()


def _write_json(payload: object, path: Path) -> None:
    """Write one JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    """Read one JSON payload."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_github_env(assignments: dict[str, str], github_env_file: Path | None) -> None:
    """Append environment exports for downstream workflow steps."""
    if github_env_file is None:
        return
    lines = [f"{key}={value}" for key, value in assignments.items()]
    with github_env_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _format_duration(seconds: float) -> str:
    """Render one duration in minutes and seconds."""
    total = int(round(seconds))
    minutes, remaining = divmod(total, 60)
    return f"{minutes}m {remaining}s"


def load_context(context_path: Path) -> FreshHostContext:
    """Load one scenario context from disk."""
    payload = _read_json(context_path)
    return FreshHostContext(**payload)


def load_report(report_path: Path) -> FreshHostReport:
    """Load one scenario report from disk."""
    payload = _read_json(report_path)
    raw_phases = payload.pop("phases", [])
    phases = [PhaseResult(**raw_phase) for raw_phase in raw_phases if isinstance(raw_phase, dict)]
    return FreshHostReport(phases=phases, **payload)


def write_report(report: FreshHostReport, report_path: Path) -> None:
    """Persist one scenario report to disk."""
    report.updated_at = _now_iso()
    _write_json(asdict(report), report_path)


def prepare_context(
    *,
    scenario_id: str,
    repo_root: Path,
    runner_temp: Path,
    workspace: Path,
    github_env_file: Path | None,
) -> FreshHostContext:
    """Create and persist one fresh-host execution context.

    Args:
        scenario_id: Scenario selector.
        repo_root: Repository root.
        runner_temp: Runner scratch root.
        workspace: GitHub workspace path.
        github_env_file: Optional GITHUB_ENV path to update.

    Returns:
        The resolved fresh-host context.
    """
    spec = _require_scenario(scenario_id)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    activate_services = not (spec.platform == "macos" and event_name == "pull_request")
    tmp_root = runner_temp / f"strongclaw-{spec.platform}-host"
    report_dir = runner_temp / "fresh-host-reports" / scenario_id
    context_dir = runner_temp / "fresh-host" / scenario_id
    app_home = tmp_root / "home"
    xdg_runtime_dir = tmp_root / "xdg-runtime" if spec.platform == "linux" else None
    diagnostics_dir = report_dir / "diagnostics"
    context_path = context_dir / "context.json"
    report_path = report_dir / "report.json"
    runtime_report_path = (
        report_dir / "runtime-install-report.json" if spec.platform == "macos" else None
    )
    image_report_path = (
        report_dir / "image-ensure-report.json" if spec.platform == "macos" else None
    )

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
        report_path=str(report_path.resolve()),
        context_path=str(context_path.resolve()),
        runtime_report_path=(
            str(runtime_report_path.resolve()) if runtime_report_path is not None else None
        ),
        image_report_path=(
            str(image_report_path.resolve()) if image_report_path is not None else None
        ),
        diagnostics_dir=str(diagnostics_dir.resolve()),
        runtime_provider=(
            os.environ.get("FRESH_HOST_RUNTIME_PROVIDER", "").strip()
            or os.environ.get("DEFAULT_MACOS_RUNTIME_PROVIDER", "").strip()
            or None
        ),
        docker_pull_parallelism=int(
            os.environ.get(
                "FRESH_HOST_DOCKER_PULL_PARALLELISM",
                str(DEFAULT_DOCKER_PULL_PARALLELISM),
            )
        ),
        docker_pull_max_attempts=int(
            os.environ.get(
                "FRESH_HOST_DOCKER_PULL_MAX_ATTEMPTS",
                str(DEFAULT_DOCKER_PULL_MAX_ATTEMPTS),
            )
        ),
        activate_services=activate_services,
        ensure_images=spec.platform == "macos" and activate_services,
        normalize_machine_name=spec.normalize_machine_name,
        verify_launchd=spec.platform == "macos" and activate_services,
        verify_rendered_files=spec.platform == "linux"
        or (spec.platform == "macos" and not activate_services),
        exercise_sidecars=activate_services,
        exercise_browser_lab=activate_services,
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
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    _write_json(asdict(context), _context_path(context.context_path))
    write_report(report, _context_path(context.report_path))
    exports = {
        "FRESH_HOST_CONTEXT": context.context_path,
        "FRESH_HOST_REPORT_DIR": context.report_dir,
        "FRESH_HOST_REPORT_JSON": context.report_path,
        "TMP_ROOT": context.tmp_root,
        "STRONGCLAW_APP_HOME": context.app_home,
    }
    if context.xdg_runtime_dir is not None:
        exports["STRONGCLAW_XDG_RUNTIME_DIR"] = context.xdg_runtime_dir
    if context.runtime_provider is not None:
        exports["FRESH_HOST_RUNTIME_PROVIDER"] = context.runtime_provider
    if context.runtime_report_path is not None:
        exports["FRESH_HOST_RUNTIME_REPORT_JSON"] = context.runtime_report_path
    if context.image_report_path is not None:
        exports["FRESH_HOST_IMAGE_REPORT_JSON"] = context.image_report_path
    _write_github_env(exports, github_env_file)
    _log(f"Prepared context for scenario={context.scenario_id} at {context.context_path}.")
    return context


def _phase_env(context: FreshHostContext) -> dict[str, str]:
    """Build the execution environment for one scenario phase."""
    env = dict(os.environ)
    path_prefix = f"{context.app_home}/.local/bin"
    env.update(
        {
            "HOME": context.app_home,
            "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
            "OPENCLAW_MODEL_SETUP_MODE": "skip",
            "PYTHONPATH": "src",
        }
    )
    if context.platform == "linux":
        env["XDG_CONFIG_HOME"] = f"{context.app_home}/.config"
        if context.xdg_runtime_dir is None:
            raise FreshHostError("Linux scenarios require xdg_runtime_dir in context.")
        env["XDG_RUNTIME_DIR"] = context.xdg_runtime_dir
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
        return env

    env["XDG_CONFIG_HOME"] = f"{context.app_home}/.config"
    env["OPENCLAW_MDNS_HOSTNAME"] = "openclaw-ci"
    env["STRONGCLAW_LAUNCHD_SIDECARS_TIMEOUT_SECONDS"] = "2700"
    env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"] = (
        f"{context.app_home}/.openclaw/repo-local-compose"
    )
    env["PATH"] = (
        f"{context.app_home}/.config/varlock/bin:{path_prefix}"
        f":/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:{env.get('PATH', '')}"
    )
    return env


def _repo_paths(context: FreshHostContext) -> tuple[Path, Path]:
    """Return the repo root and app home paths."""
    return _repo_root(context.repo_root), _context_path(context.app_home)


def _system_clawops_command(*arguments: str) -> list[str]:
    """Return the bootstrap-time clawops command."""
    return [sys.executable, "-m", "clawops", *arguments]


def _venv_clawops_command(context: FreshHostContext, *arguments: str) -> list[str]:
    """Return the managed-environment clawops command."""
    repo_root = _repo_root(context.repo_root)
    # Preserve the venv entrypoint path instead of resolving the symlink target.
    return [str(repo_root / ".venv" / "bin" / "python"), "-m", "clawops", *arguments]


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    check: bool = True,
) -> None:
    """Run one inherited subprocess command."""
    _log("Running: " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        timeout=timeout_seconds,
        text=True,
    )
    if check and completed.returncode != 0:
        raise FreshHostError(
            f"Command failed with exit {completed.returncode}: {' '.join(command)}"
        )


def _ensure_dir(path: Path, *, mode: int | None = None) -> None:
    """Create one directory and optionally apply mode bits."""
    path.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        path.chmod(mode)


def _verify_file_exists(path: Path) -> None:
    """Raise when the requested file is missing."""
    if not path.is_file():
        raise FreshHostError(f"Expected file is missing: {path}")


def _linux_bootstrap(context: FreshHostContext) -> list[str]:
    """Bootstrap the Linux host."""
    repo_root, app_home = _repo_paths(context)
    env = _phase_env(context)
    xdg_runtime_dir = _context_path(context.xdg_runtime_dir or "")
    _ensure_dir(app_home)
    _ensure_dir(xdg_runtime_dir, mode=stat.S_IRWXU)
    command = _system_clawops_command()
    return _run_clawops_bootstrap(command, repo_root, env, context)


def _run_clawops_bootstrap(
    command: list[str],
    repo_root: Path,
    env: dict[str, str],
    context: FreshHostContext,
) -> list[str]:
    """Run one bootstrap command."""
    full_command = [
        *command,
        "bootstrap",
        "--repo-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        "openclaw-default",
    ]
    _run_command(full_command, cwd=repo_root, env=env)
    return full_command


def _linux_setup(context: FreshHostContext) -> list[str]:
    """Run the Linux setup flow."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    command = _venv_clawops_command(
        context,
        "setup",
        "--repo-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        "openclaw-default",
        "--non-interactive",
        "--no-verify",
        "--no-activate-services",
    )
    _run_command(command, cwd=repo_root, env=env)
    return command


def _verify_linux_rendered_units(context: FreshHostContext) -> None:
    """Verify Linux service files were rendered."""
    unit_root = _context_path(context.app_home) / ".config" / "systemd" / "user"
    for filename in (
        "openclaw-gateway.service",
        "openclaw-sidecars.service",
        "openclaw-browserlab.service",
    ):
        _verify_file_exists(unit_root / filename)


def _exercise_linux_sidecars(context: FreshHostContext) -> list[str]:
    """Exercise Linux repo-local sidecars."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    up_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "sidecars",
        "up",
        "--repo-local-state",
    )
    down_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "sidecars",
        "down",
        "--repo-local-state",
    )
    _run_command(up_command, cwd=repo_root, env=env)
    _run_command(down_command, cwd=repo_root, env=env)
    return down_command


def _exercise_linux_browser_lab(context: FreshHostContext) -> list[str]:
    """Exercise Linux repo-local browser-lab."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    up_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "up",
        "--repo-local-state",
    )
    down_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "down",
        "--repo-local-state",
    )
    _run_command(up_command, cwd=repo_root, env=env)
    _run_command(down_command, cwd=repo_root, env=env)
    return down_command


def _normalize_macos_machine_name(_: FreshHostContext) -> list[str]:
    """Normalize the hosted macOS machine name."""
    commands = [
        ["sudo", "scutil", "--set", "ComputerName", "openclaw-ci"],
        ["sudo", "scutil", "--set", "LocalHostName", "openclaw-ci"],
        ["sudo", "scutil", "--set", "HostName", "openclaw-ci"],
        ["scutil", "--get", "ComputerName"],
        ["scutil", "--get", "LocalHostName"],
    ]
    for command in commands:
        _run_command(command, cwd=Path.cwd(), env=dict(os.environ))
    return commands[-1]


def _macos_bootstrap(context: FreshHostContext) -> list[str]:
    """Bootstrap the macOS host."""
    repo_root, app_home = _repo_paths(context)
    env = _phase_env(context)
    _ensure_dir(app_home)
    command = _run_clawops_bootstrap(_system_clawops_command(), repo_root, env, context)
    return command


def _macos_setup(context: FreshHostContext) -> list[str]:
    """Run the macOS setup flow."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    _ensure_dir(_context_path(env["STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR"]))
    command = _venv_clawops_command(
        context,
        "setup",
        "--repo-root",
        ".",
        "--home-dir",
        context.app_home,
        "--profile",
        "openclaw-default",
        "--non-interactive",
        "--no-verify",
    )
    if not context.activate_services:
        command.append("--no-activate-services")
    _run_command(command, cwd=repo_root, env=env)
    return command


def _verify_macos_launchd(context: FreshHostContext) -> None:
    """Verify macOS launchd outputs."""
    home_dir = _context_path(context.app_home)
    launch_agents = home_dir / "Library" / "LaunchAgents"
    for filename in (
        "ai.openclaw.gateway.plist",
        "ai.openclaw.sidecars.plist",
        "ai.openclaw.browserlab.plist",
    ):
        _verify_file_exists(launch_agents / filename)
    if not context.verify_launchd:
        return
    env = _phase_env(context)
    domain = f"gui/{os.getuid()}"
    commands = [
        ["launchctl", "print", f"{domain}/ai.openclaw.gateway"],
        ["launchctl", "print", f"{domain}/ai.openclaw.sidecars"],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            cwd=_repo_root(context.repo_root),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip() or "launchctl failed"
            raise FreshHostError(output)
        output = result.stdout
        if "gateway" in command[-1] and "state = running" not in output:
            raise FreshHostError("gateway launchd service is not running")
        if "sidecars" in command[-1] and "last exit code = 0" not in output:
            raise FreshHostError("sidecars launchd service did not exit cleanly")


def _exercise_macos_sidecars(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local sidecars."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    up_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "sidecars",
        "up",
        "--repo-local-state",
    )
    down_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "sidecars",
        "down",
        "--repo-local-state",
    )
    _run_command(up_command, cwd=repo_root, env=env)
    _run_command(down_command, cwd=repo_root, env=env)
    return down_command


def _exercise_macos_browser_lab(context: FreshHostContext) -> list[str]:
    """Exercise macOS repo-local browser-lab."""
    repo_root, _ = _repo_paths(context)
    env = _phase_env(context)
    up_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "up",
        "--repo-local-state",
    )
    down_command = _venv_clawops_command(
        context,
        "ops",
        "--repo-root",
        ".",
        "browser-lab",
        "down",
        "--repo-local-state",
    )
    _run_command(up_command, cwd=repo_root, env=env)
    _run_command(down_command, cwd=repo_root, env=env)
    return down_command


def _best_effort(command: list[str], *, cwd: Path, env: dict[str, str]) -> str | None:
    """Run one best-effort command and return a warning on failure."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{' '.join(command)} failed: {exc}"
    if completed.returncode == 0:
        return None
    output = completed.stderr.strip() or completed.stdout.strip() or "command failed"
    return f"{' '.join(command)} failed: {output}"


def _cleanup_macos(context: FreshHostContext) -> list[str]:
    """Best-effort cleanup for macOS launchd and compose state."""
    if not context.activate_services:
        return []
    repo_root, home_dir = _repo_paths(context)
    env = _phase_env(context)
    xdg_runtime_dir = home_dir / ".xdg-runtime"
    _ensure_dir(xdg_runtime_dir, mode=stat.S_IRWXU)
    env["XDG_RUNTIME_DIR"] = str(xdg_runtime_dir)
    warnings: list[str] = []
    domain = f"gui/{os.getuid()}"
    launch_agents = home_dir / "Library" / "LaunchAgents"
    commands = [
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.gateway.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.sidecars.plist")],
        ["launchctl", "bootout", domain, str(launch_agents / "ai.openclaw.browserlab.plist")],
        _venv_clawops_command(context, "ops", "--repo-root", ".", "sidecars", "down"),
        _venv_clawops_command(context, "ops", "--repo-root", ".", "browser-lab", "down"),
    ]
    for command in commands:
        warning = _best_effort(command, cwd=repo_root, env=env)
        if warning is not None:
            warnings.append(warning)
    if warnings:
        for warning in warnings:
            _log(warning)
    return commands[-1]


def scenario_phase_names(context: FreshHostContext) -> list[str]:
    """Return the ordered phase plan for one scenario context."""
    if context.platform == "linux":
        return [
            "bootstrap",
            "setup",
            "verify-rendered-files",
            "exercise-sidecars",
            "exercise-browser-lab",
        ]

    phase_names = ["normalize-machine-name", "bootstrap", "setup", "verify-rendered-files"]
    if context.exercise_sidecars:
        phase_names.append("exercise-sidecars")
    if context.exercise_browser_lab:
        phase_names.append("exercise-browser-lab")
    return phase_names


def _run_named_phase(context: FreshHostContext, phase_name: str) -> list[str] | None:
    """Execute one named scenario phase."""
    if phase_name == "bootstrap":
        return (
            _linux_bootstrap(context) if context.platform == "linux" else _macos_bootstrap(context)
        )
    if phase_name == "setup":
        return _linux_setup(context) if context.platform == "linux" else _macos_setup(context)
    if phase_name == "verify-rendered-files":
        if context.platform == "linux":
            _verify_linux_rendered_units(context)
        else:
            _verify_macos_launchd(context)
        return None
    if phase_name == "exercise-sidecars":
        return (
            _exercise_linux_sidecars(context)
            if context.platform == "linux"
            else _exercise_macos_sidecars(context)
        )
    if phase_name == "exercise-browser-lab":
        return (
            _exercise_linux_browser_lab(context)
            if context.platform == "linux"
            else _exercise_macos_browser_lab(context)
        )
    if phase_name == "normalize-machine-name":
        return _normalize_macos_machine_name(context)
    raise FreshHostError(f"Unsupported phase '{phase_name}'.")


def _record_phase(
    *,
    report: FreshHostReport,
    report_path: Path,
    phase_name: str,
    action: Callable[[], list[str] | None],
) -> None:
    """Execute one phase and append the structured result."""
    started_at = _now_iso()
    started = time.monotonic()
    command: list[str] | None = None
    try:
        command = action()
    except Exception as exc:  # noqa: BLE001
        phase = PhaseResult(
            name=phase_name,
            status="failure",
            duration_seconds=round(time.monotonic() - started, 3),
            started_at=started_at,
            finished_at=_now_iso(),
            command=command,
            failure_reason=str(exc),
        )
        report.phases.append(phase)
        report.failure_reason = str(exc)
        report.status = "failure"
        write_report(report, report_path)
        raise FreshHostError(str(exc)) from exc

    phase = PhaseResult(
        name=phase_name,
        status="success",
        duration_seconds=round(time.monotonic() - started, 3),
        started_at=started_at,
        finished_at=_now_iso(),
        command=command,
    )
    report.phases.append(phase)
    write_report(report, report_path)


def run_scenario(context_path: Path) -> FreshHostReport:
    """Run the configured phase plan for one scenario."""
    context = load_context(context_path)
    report_path = _context_path(context.report_path)
    report = load_report(report_path)
    report.status = "running"
    write_report(report, report_path)
    for phase_name in scenario_phase_names(context):
        _log(f"Starting phase={phase_name}.")
        _record_phase(
            report=report,
            report_path=report_path,
            phase_name=phase_name,
            action=lambda phase_name=phase_name: _run_named_phase(context, phase_name),
        )
    report.status = "success"
    write_report(report, report_path)
    return report


def _capture_to_file(
    command: list[str],
    *,
    output_path: Path,
    cwd: Path,
    env: dict[str, str],
) -> str | None:
    """Run one best-effort command and capture stdout/stderr to a file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        output_path.write_text(f"{exc}\n", encoding="utf-8")
        return f"{' '.join(command)} failed: {exc}"
    output_path.write_text(
        "\n".join(chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk)
        + "\n",
        encoding="utf-8",
    )
    if completed.returncode == 0:
        return None
    return f"{' '.join(command)} exited with {completed.returncode}"


def collect_diagnostics(context_path: Path) -> FreshHostReport:
    """Collect best-effort diagnostics for the active scenario."""
    context = load_context(context_path)
    report_path = _context_path(context.report_path)
    report = load_report(report_path)
    repo_root = _repo_root(context.repo_root)
    env = _phase_env(context)
    diagnostics_dir = _context_path(context.diagnostics_dir)
    started_at = _now_iso()
    started = time.monotonic()
    notes: list[str] = []
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    if context.platform == "linux":
        commands = {
            diagnostics_dir / "docker-info.txt": ["docker", "info"],
            diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
            diagnostics_dir / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
        }
    else:
        commands = {
            diagnostics_dir / "docker-info.txt": ["docker", "info"],
            diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
            diagnostics_dir / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
            diagnostics_dir
            / "launchctl-gateway.txt": [
                "launchctl",
                "print",
                f"gui/{os.getuid()}/ai.openclaw.gateway",
            ],
            diagnostics_dir
            / "launchctl-sidecars.txt": [
                "launchctl",
                "print",
                f"gui/{os.getuid()}/ai.openclaw.sidecars",
            ],
            diagnostics_dir / "docker-ps.txt": ["docker", "ps", "-a"],
        }
        if shutil.which("colima") is not None:
            commands[diagnostics_dir / "colima-status.txt"] = ["colima", "status"]
            commands[diagnostics_dir / "colima-list.txt"] = ["colima", "list"]
    for output_path, command in commands.items():
        note = _capture_to_file(command, output_path=output_path, cwd=repo_root, env=env)
        if note is not None:
            notes.append(note)
            _log(note)
    phase = PhaseResult(
        name="collect-diagnostics",
        status="success",
        duration_seconds=round(time.monotonic() - started, 3),
        started_at=started_at,
        finished_at=_now_iso(),
        command=None,
        notes=notes,
    )
    report.phases.append(phase)
    write_report(report, report_path)
    return report


def cleanup(context_path: Path) -> FreshHostReport:
    """Run best-effort scenario cleanup."""
    context = load_context(context_path)
    report_path = _context_path(context.report_path)
    report = load_report(report_path)
    started_at = _now_iso()
    started = time.monotonic()
    command: list[str] | None = None
    notes: list[str] = []
    if context.platform == "macos":
        command = _cleanup_macos(context)
    phase = PhaseResult(
        name="cleanup",
        status="success",
        duration_seconds=round(time.monotonic() - started, 3),
        started_at=started_at,
        finished_at=_now_iso(),
        command=command,
        notes=notes,
    )
    report.phases.append(phase)
    write_report(report, report_path)
    return report


def write_summary(context_path: Path, summary_file: Path) -> None:
    """Render one GitHub step summary for the scenario report."""
    context = load_context(context_path)
    report = load_report(_context_path(context.report_path))
    lines: list[str] = [
        f"## {report.job_name}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Scenario | {report.scenario_id} |",
        f"| Platform | {report.platform} |",
        f"| Status | {report.status} |",
        f"| Runtime provider | {report.runtime_provider or 'n/a'} |",
        f"| Activate services | {context.activate_services} |",
        "",
    ]
    if report.phases:
        lines.extend(
            [
                "| Phase | Status | Duration |",
                "| --- | --- | --- |",
            ]
        )
        total_duration = 0.0
        for phase in report.phases:
            total_duration += phase.duration_seconds
            lines.append(
                f"| {phase.name} | {phase.status} | {_format_duration(phase.duration_seconds)} |"
            )
        lines.extend(["", f"Known phase total: {_format_duration(total_duration)}", ""])
    else:
        lines.extend(["No phase results were recorded.", ""])

    if report.image_report_path is not None and _context_path(report.image_report_path).is_file():
        image_report = _read_json(_context_path(report.image_report_path))
        lines.extend(
            [
                "| Image ensure field | Value |",
                "| --- | --- |",
                f"| Images requested | {len(image_report.get('images', []))} |",
                f"| Missing before pull | {len(image_report.get('missing_before_pull', []))} |",
                f"| Pull attempts | {image_report.get('pull_attempt_count')} |",
                f"| Retried images | {len(image_report.get('retried_images', []))} |",
                f"| Pulled images | {len(image_report.get('pulled_images', []))} |",
                f"| Failure reason | {image_report.get('failure_reason')} |",
                "",
            ]
        )
    if (
        report.runtime_report_path is not None
        and _context_path(report.runtime_report_path).is_file()
    ):
        runtime_report = _read_json(_context_path(report.runtime_report_path))
        lines.extend(
            [
                "| Runtime field | Value |",
                "| --- | --- |",
                f"| Runtime provider | {runtime_report.get('runtime_provider')} |",
                f"| Host CPU count | {runtime_report.get('host_cpu_count')} |",
                f"| Host memory GiB | {runtime_report.get('host_memory_gib')} |",
                f"| Docker host | {runtime_report.get('docker_host')} |",
                f"| Failure reason | {runtime_report.get('failure_reason')} |",
                "",
            ]
        )
    lines.append(f"Diagnostics artifact root: `{report.diagnostics_dir}`")
    lines.append("")
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
