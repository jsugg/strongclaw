"""Public hosted-docker helper facade."""

from tests.utils.helpers._hosted_docker.diagnostics import collect_runtime_diagnostics
from tests.utils.helpers._hosted_docker.images import (
    ensure_images,
    list_local_images,
    pull_images,
    resolve_compose_images,
)
from tests.utils.helpers._hosted_docker.models import (
    DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
    PULL_HEARTBEAT_SECONDS,
    ImageEnsureReport,
    PullReport,
    RuntimeInstallReport,
    RuntimeRecoveryAttemptReport,
)
from tests.utils.helpers._hosted_docker.pull_client import (
    STALL_TIMEOUT_SECONDS,
    CliDockerPullClient,
    DockerPullClient,
    PullOutcome,
    classify_pull_failure,
)
from tests.utils.helpers._hosted_docker.runtime import (
    setup_orbstack,
    wait_runtime_ready,
)
from tests.utils.helpers._hosted_docker.shell import run_checked as _run_checked
from tests.utils.helpers._hosted_docker.shell import run_command as _run_command
from tests.utils.helpers._hosted_docker.shell import wait_for_docker_ready as _wait_for_docker_ready

__all__ = [
    "CliDockerPullClient",
    "DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS",
    "DockerPullClient",
    "ImageEnsureReport",
    "PULL_HEARTBEAT_SECONDS",
    "PullOutcome",
    "PullReport",
    "STALL_TIMEOUT_SECONDS",
    "RuntimeRecoveryAttemptReport",
    "RuntimeInstallReport",
    "_run_checked",
    "_run_command",
    "_wait_for_docker_ready",
    "classify_pull_failure",
    "collect_runtime_diagnostics",
    "ensure_images",
    "list_local_images",
    "pull_images",
    "setup_orbstack",
    "wait_runtime_ready",
    "resolve_compose_images",
]
