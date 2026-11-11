"""Best-effort lane diagnostics: service-manager state and gateway logs, secrets redacted."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import varlock_local_env_file
from tests.utils.helpers._feature_verification.redaction import redact, secret_values
from tests.utils.helpers._fresh_host.shell import phase_env
from tests.utils.helpers._fresh_host.storage import load_context

LAUNCHD_LABELS: Final[tuple[str, ...]] = (
    "ai.openclaw.gateway",
    "ai.openclaw.sidecars",
    "ai.openclaw.backup-create",
    "ai.openclaw.backup-verify",
    "ai.openclaw.maintenance",
)
_LOG_TAIL_CHARS: Final[int] = 400_000
_COMMAND_TIMEOUT_SECONDS: Final[int] = 60


def _diagnostic_commands(platform: str) -> dict[str, list[str]]:
    if platform == "linux":
        return {
            "systemd-units.txt": ["systemctl", "--user", "list-units", "openclaw*", "--all"],
            "systemd-timers.txt": ["systemctl", "--user", "list-timers", "--all"],
            "systemd-status.txt": ["systemctl", "--user", "status", "openclaw*", "--no-pager"],
            "systemd-journal.txt": ["journalctl", "--user", "--no-pager", "-n", "2000"],
            "docker-ps.txt": ["docker", "ps", "-a"],
        }
    commands = {
        f"launchctl-{label}.txt": ["launchctl", "print", f"gui/{os.getuid()}/{label}"]
        for label in LAUNCHD_LABELS
    }
    commands["docker-ps.txt"] = ["docker", "ps", "-a"]
    return commands


def collect_lane_diagnostics(*, context_file: Path, output_dir: Path) -> None:
    """Capture service-manager state and gateway logs into *output_dir*."""
    context = load_context(context_file)
    env = phase_env(context)
    secrets = secret_values(
        varlock_local_env_file(
            Path(context.repo_root), home_dir=Path(context.app_home), environ=env
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for name, command in _diagnostic_commands(context.platform).items():
        try:
            completed = subprocess.run(  # noqa: S603 - fixed diagnostic argv
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
            text = (
                f"$ {' '.join(command)}\nexit={completed.returncode}\n"
                f"{completed.stdout}\n{completed.stderr}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            text = f"$ {' '.join(command)}\nfailed: {exc}"
        (output_dir / name).write_text(redact(text, secrets), encoding="utf-8")
    for log_root in (Path("/tmp/openclaw"), Path(context.app_home) / ".openclaw" / "logs"):
        if not log_root.is_dir():
            continue
        for log_file in sorted(log_root.glob("*.log"))[-5:]:
            tail = log_file.read_text(encoding="utf-8", errors="replace")[-_LOG_TAIL_CHARS:]
            (output_dir / f"{log_root.name}-{log_file.name}").write_text(
                redact(tail, secrets), encoding="utf-8"
            )
