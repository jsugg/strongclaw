"""Detached lifecycle for the fake model server used by feature-verification lanes.

Workflow steps are one-shot shell commands, so the server runs as a detached child that
outlives the step that started it; its pid and bound ports live in a small state file.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from tests.utils.helpers._feature_verification.fake_model import HEALTH_PATH, JOURNAL_PATH

STATE_FILE_NAME: Final[str] = "fake-model.json"
LOG_FILE_NAME: Final[str] = "fake-model.log"
READY_TIMEOUT_SECONDS: Final[float] = 20.0
STOP_TIMEOUT_SECONDS: Final[float] = 10.0
_POLL_SECONDS: Final[float] = 0.2


class FakeModelProcessError(RuntimeError):
    """Raised when the detached fake model server cannot be managed."""


def _probe_host(host: str) -> str:
    """Return the address a local client should use for *host*."""
    return "127.0.0.1" if host in {"0.0.0.0", "", "::"} else host


def _healthy(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310 - loopback probe
            f"http://{_probe_host(host)}:{port}{HEALTH_PATH}", timeout=2
        ) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def start_detached(
    *,
    script_path: Path,
    host: str,
    ports: Sequence[int],
    state_dir: Path,
) -> dict[str, object]:
    """Start ``serve-fake-model`` detached and block until every port answers."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / LOG_FILE_NAME
    command = [sys.executable, str(script_path), "serve-fake-model", "--host", host]
    for port in ports:
        command.extend(["--port", str(port)])
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(  # noqa: S603 - fixed argv built above
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise FakeModelProcessError(
                f"fake model server exited with {process.returncode}; see {log_path}"
            )
        if all(_healthy(host, port) for port in ports):
            state: dict[str, object] = {
                "pid": process.pid,
                "host": host,
                "ports": list(ports),
                "log": str(log_path),
            }
            (state_dir / STATE_FILE_NAME).write_text(json.dumps(state), encoding="utf-8")
            return state
        time.sleep(_POLL_SECONDS)
    process.terminate()
    raise FakeModelProcessError(f"fake model server not ready in {READY_TIMEOUT_SECONDS}s")


def load_state(state_dir: Path) -> dict[str, object]:
    """Return the persisted server state."""
    state_path = state_dir / STATE_FILE_NAME
    if not state_path.is_file():
        raise FakeModelProcessError(f"no fake model state at {state_path}")
    payload: object = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FakeModelProcessError(f"invalid fake model state at {state_path}")
    return cast(dict[str, object], payload)


def fetch_journal(host: str, port: int) -> list[dict[str, object]]:
    """Return the request journal recorded by a running server."""
    with urllib.request.urlopen(  # noqa: S310 - loopback probe
        f"http://{_probe_host(host)}:{port}{JOURNAL_PATH}", timeout=5
    ) as response:
        payload: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise FakeModelProcessError("fake model journal is not a JSON object")
    requests = cast(dict[str, object], payload).get("requests")
    if not isinstance(requests, list):
        raise FakeModelProcessError("fake model journal has no request list")
    return [cast(dict[str, object], entry) for entry in cast(list[object], requests)]


def stop_detached(state_dir: Path) -> bool:
    """Terminate the detached server; return whether a live process was stopped."""
    state = load_state(state_dir)
    pid = state.get("pid")
    if not isinstance(pid, int):
        raise FakeModelProcessError("fake model state has no pid")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(_POLL_SECONDS)
    os.kill(pid, signal.SIGKILL)
    return True
