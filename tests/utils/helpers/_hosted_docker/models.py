"""Data models and constants for hosted Docker helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

LOG_PREFIX: Final[str] = "[hosted-docker]"
DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS: Final[int] = 1800
PULL_HEARTBEAT_SECONDS: Final[int] = 30


@dataclass(slots=True)
class PullReport:
    """Structured report describing one image pull sequence."""

    exit_code: int
    pulled_images: list[str]
    failed_images: list[str]
    attempt_count: int
    retried_images: list[str]


@dataclass(slots=True)
class RuntimeRecoveryAttemptReport:
    """Structured report describing one runtime recovery attempt."""

    attempt: int
    trigger_reason: str
    status: str
    recovery_commands: list[list[str]]
    recovery_exit_code: int | None
    recovery_timed_out: bool
    readiness_reason: str | None
    before_diagnostics_dir: str
    after_diagnostics_dir: str
    recovery_output_path: str
    started_at: str
    finished_at: str


def _empty_recovery_attempts() -> list[RuntimeRecoveryAttemptReport]:
    """Return a typed empty recovery-attempt list."""
    return []


@dataclass(slots=True)
class RuntimeInstallReport:
    """Structured report describing runtime installation."""

    runtime_provider: str
    arch: str
    host_cpu_count: int | None
    host_memory_gib: int | None
    docker_host: str | None
    docker_config: str | None
    installed_tools: list[str]
    failure_reason: str | None
    started_at: str
    finished_at: str
    duration_seconds: float
    created_at: str
    failure_phase: str | None = None
    recovery_attempt_count: int = 0
    recovery_attempts: list[RuntimeRecoveryAttemptReport] = field(
        default_factory=_empty_recovery_attempts
    )


@dataclass(slots=True)
class ImageEnsureReport:
    """Structured report describing image ensure status."""

    compose_files: list[str]
    images: list[str]
    local_before: list[str]
    missing_before_pull: list[str]
    pulled_images: list[str]
    missing_after_pull: list[str]
    pull_parallelism: int
    pull_attempt_count: int
    retried_images: list[str]
    failure_reason: str | None
    started_at: str
    finished_at: str
    duration_seconds: float
    created_at: str
