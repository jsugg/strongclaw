"""Public fresh-host helper facade."""

from tests.utils.helpers._fresh_host.context import prepare_context, preview_context
from tests.utils.helpers._fresh_host.macos import (
    deactivate_macos_host_services as _deactivate_macos_host_services,
)
from tests.utils.helpers._fresh_host.models import (
    DEFAULT_DOCKER_PULL_MAX_ATTEMPTS,
    DEFAULT_DOCKER_PULL_PARALLELISM,
    FreshHostContext,
    FreshHostError,
    FreshHostReport,
    PhaseResult,
    PhaseStatus,
    PlatformName,
    ScenarioId,
    ScenarioSpec,
)
from tests.utils.helpers._fresh_host.reporting import cleanup, collect_diagnostics, write_summary
from tests.utils.helpers._fresh_host.scenario import run_named_phase as _run_named_phase
from tests.utils.helpers._fresh_host.scenario import run_scenario, scenario_phase_names
from tests.utils.helpers._fresh_host.shell import venv_clawops_command as _venv_clawops_command
from tests.utils.helpers._fresh_host.storage import load_context, load_report, write_report

__all__ = [
    "DEFAULT_DOCKER_PULL_MAX_ATTEMPTS",
    "DEFAULT_DOCKER_PULL_PARALLELISM",
    "FreshHostContext",
    "FreshHostError",
    "FreshHostReport",
    "PhaseResult",
    "PlatformName",
    "PhaseStatus",
    "ScenarioId",
    "ScenarioSpec",
    "_deactivate_macos_host_services",
    "_run_named_phase",
    "_venv_clawops_command",
    "cleanup",
    "collect_diagnostics",
    "load_context",
    "load_report",
    "prepare_context",
    "preview_context",
    "run_scenario",
    "scenario_phase_names",
    "write_report",
    "write_summary",
]
