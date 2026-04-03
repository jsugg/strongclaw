"""Fresh-host data model and static scenario definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

ScenarioId = Literal["linux", "macos-sidecars", "macos-browser-lab"]
PlatformName = Literal["linux", "macos"]
FreshnessMode = Literal["warm", "cold"]
PhaseStatus = Literal["success", "failure", "skipped"]

LOG_PREFIX: Final[str] = "[fresh-host]"
DEFAULT_DOCKER_PULL_PARALLELISM: Final[int] = 2
DEFAULT_DOCKER_PULL_MAX_ATTEMPTS: Final[int] = 3


def _empty_notes() -> list[str]:
    """Return an empty note list for phase results."""
    return []


class FreshHostError(RuntimeError):
    """Raised when a fresh-host operation fails."""


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Static scenario configuration."""

    scenario_id: ScenarioId
    platform: PlatformName
    job_name: str
    compose_files: tuple[str, ...]
    compose_variant: str | None
    phase_names: tuple[str, ...]
    activate_services: bool
    ensure_images: bool
    normalize_machine_name: bool
    verify_launchd: bool
    verify_rendered_files: bool
    exercise_sidecars: bool
    exercise_browser_lab: bool


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
    notes: list[str] = field(default_factory=_empty_notes)


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
    profile: str
    runtime_provider: str | None
    freshness_mode: FreshnessMode
    docker_pull_parallelism: int
    docker_pull_max_attempts: int
    compose_variant: str | None
    activate_services: bool
    ensure_images: bool
    normalize_machine_name: bool
    verify_launchd: bool
    verify_rendered_files: bool
    exercise_sidecars: bool
    exercise_browser_lab: bool
    phase_names: list[str]
    compose_files: list[str]


@dataclass(slots=True)
class FreshHostReport:
    """Structured report describing one fresh-host workflow run."""

    scenario_id: str
    job_name: str
    platform: str
    runtime_provider: str | None
    freshness_mode: FreshnessMode
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
        compose_variant=None,
        phase_names=(
            "bootstrap",
            "setup",
            "verify-rendered-files",
            "exercise-sidecars",
            "exercise-channels-runtime",
            "exercise-recovery-smoke",
            "exercise-browser-lab",
        ),
        activate_services=False,
        ensure_images=True,
        normalize_machine_name=False,
        verify_launchd=False,
        verify_rendered_files=True,
        exercise_sidecars=True,
        exercise_browser_lab=True,
    ),
    "macos-sidecars": ScenarioSpec(
        scenario_id="macos-sidecars",
        platform="macos",
        job_name="macOS Fresh Host Sidecars",
        compose_files=("platform/compose/docker-compose.aux-stack.ci-hosted-macos.yaml",),
        compose_variant="ci-hosted-macos",
        phase_names=(
            "normalize-machine-name",
            "bootstrap",
            "setup",
            "verify-launchd",
            "deactivate-services",
            "exercise-sidecars",
            "exercise-channels-runtime",
            "exercise-recovery-smoke",
        ),
        activate_services=True,
        ensure_images=True,
        normalize_machine_name=True,
        verify_launchd=True,
        verify_rendered_files=False,
        exercise_sidecars=True,
        exercise_browser_lab=False,
    ),
    "macos-browser-lab": ScenarioSpec(
        scenario_id="macos-browser-lab",
        platform="macos",
        job_name="macOS Fresh Host Browser Lab",
        compose_files=("platform/compose/docker-compose.browser-lab.ci-hosted-macos.yaml",),
        compose_variant="ci-hosted-macos",
        phase_names=("bootstrap", "setup", "exercise-browser-lab"),
        activate_services=False,
        ensure_images=True,
        normalize_machine_name=False,
        verify_launchd=False,
        verify_rendered_files=False,
        exercise_sidecars=False,
        exercise_browser_lab=True,
    ),
}
