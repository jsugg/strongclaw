"""Lane provisioning and execution steps for the feature-verification workflow."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import set_env_assignment, varlock_local_env_file
from tests.utils.helpers._feature_verification.envvars import (
    CONTEXT_ENV,
    FAKE_MODEL_DIR_ENV,
    LANE_ENV,
    MANIFEST_ENV,
    RESULTS_ENV,
    WORK_DIR_ENV,
)
from tests.utils.helpers._fresh_host.budget import (
    SYSTEM_CLOCK,
    JobBudget,
    scenario_phase_deadline,
)
from tests.utils.helpers._fresh_host.shell import phase_env
from tests.utils.helpers._fresh_host.storage import load_context

FEATURE_SUITE_PATH: Final[str] = "tests/suites/e2e/feature_verification"
FAKE_CHAT_MODEL_REF: Final[str] = "vllm/featver-chat"
# vLLM is a keyless local provider; OpenClaw only needs a non-empty key to register it.
FAKE_PROVIDER_ENV: Final[dict[str, str]] = {
    "OPENCLAW_DEFAULT_MODEL": FAKE_CHAT_MODEL_REF,
    "VLLM_API_KEY": "featver-local",
    "HYPERMEMORY_EMBEDDING_MODEL": "ollama/nomic-embed-text",
    "HYPERMEMORY_EMBEDDING_API_BASE": "http://host.docker.internal:11434",
}
DEFAULT_LANE_TIMEOUT_SECONDS: Final[int] = 3600


class LaneError(RuntimeError):
    """Raised when a lane step cannot run."""


LINGER_READY_TIMEOUT_SECONDS: Final[float] = 60.0
_LINGER_POLL_SECONDS: Final[float] = 1.0


def configure_model_provider(context_file: Path) -> Path:
    """Point the operator env contract at the fake model server, as an operator edits it."""
    context = load_context(context_file)
    env = phase_env(context)
    env_file = varlock_local_env_file(
        Path(context.repo_root), home_dir=Path(context.app_home), environ=env
    )
    env_file.parent.mkdir(parents=True, exist_ok=True)
    for key, value in FAKE_PROVIDER_ENV.items():
        set_env_assignment(env_file, key, value)
    env_file.chmod(0o600)
    return env_file


def enable_systemd_user_manager() -> Path:
    """Start the runner's systemd user manager (lingering) and wait for its bus."""
    user = (
        os.environ.get("USER")
        or subprocess.run(["id", "-un"], check=True, capture_output=True, text=True).stdout.strip()
    )
    subprocess.run(["sudo", "loginctl", "enable-linger", user], check=True)
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    bus = runtime_dir / "bus"
    deadline = time.monotonic() + LINGER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if bus.exists():
            probe = subprocess.run(
                ["systemctl", "--user", "is-system-running"],
                env={**os.environ, "XDG_RUNTIME_DIR": str(runtime_dir)},
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.stdout.strip() in {"running", "degraded", "starting"}:
                return runtime_dir
        time.sleep(_LINGER_POLL_SECONDS)
    raise LaneError(f"systemd user manager bus {bus} did not come up")


def _lane_timeout_seconds() -> int:
    budget = JobBudget.from_env(os.environ)
    if budget is None:
        return DEFAULT_LANE_TIMEOUT_SECONDS
    return max(60, int(scenario_phase_deadline(budget).remaining(SYSTEM_CLOCK)))


def run_lane(
    *,
    context_file: Path,
    lane_id: str,
    results_path: Path,
    fake_model_dir: Path | None,
    manifest_path: Path | None = None,
) -> int:
    """Run the lane's feature tests against the provisioned host; return pytest's exit code."""
    context = load_context(context_file)
    repo_root = Path(context.repo_root)
    # The pytest session validates the lane against the matrix (this step runs without PyYAML).
    manifest_file = manifest_path or repo_root / "tests" / "feature_matrix.yaml"
    env = phase_env(context)
    env.update(
        {
            LANE_ENV: lane_id,
            CONTEXT_ENV: str(context_file),
            RESULTS_ENV: str(results_path),
            WORK_DIR_ENV: str(Path(context.tmp_root) / "featver"),
            MANIFEST_ENV: str(manifest_file),
        }
    )
    if fake_model_dir is not None:
        env[FAKE_MODEL_DIR_ENV] = str(fake_model_dir)
    command = [
        str(repo_root / ".venv" / "bin" / "python"),
        "-m",
        "pytest",
        FEATURE_SUITE_PATH,
        "-m",
        "feature_verification",
        "-p",
        "no:cacheprovider",
        "-rA",
        "--durations=15",
    ]
    timeout = _lane_timeout_seconds()
    print(f"[feature-verification] lane={lane_id} timeout={timeout}s: {' '.join(command)}")
    try:
        completed = subprocess.run(command, cwd=repo_root, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LaneError(f"lane {lane_id} exceeded its {timeout}s budget") from exc
    if not results_path.is_file():
        raise LaneError(f"lane {lane_id} wrote no results to {results_path}")
    return completed.returncode
