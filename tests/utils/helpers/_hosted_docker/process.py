"""Process-group supervision so a timed-out command cannot outlive its deadline slice."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any, Final

TERMINATE_GRACE_SECONDS: Final[float] = 5.0


def terminate_process_group(
    process: subprocess.Popen[Any], *, grace_seconds: float | None = None
) -> None:
    """TERM the child's process group, KILL it after a short grace, and reap the child.

    The child must have been started with `start_new_session=True`, so its pid is the group
    id. KILL is sent even when the leader exits within the grace, so grandchildren that
    ignore TERM do not survive their leader.
    """
    if grace_seconds is None:
        grace_seconds = TERMINATE_GRACE_SECONDS
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        pass  # macOS reports EPERM for a group whose members are all zombies.
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
