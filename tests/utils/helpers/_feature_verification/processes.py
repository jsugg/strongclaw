"""Detached lifecycle for the fake model server used by feature-verification lanes.

Workflow steps are one-shot shell commands, so the server runs as a detached child that
outlives the step that started it; its pid and bound ports live in a small state file.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from tests.utils.helpers._feature_verification.fake_model import HEALTH_PATH, JOURNAL_PATH

STATE_FILE_NAME: Final[str] = "fake-model.json"
LOG_FILE_NAME: Final[str] = "fake-model.log"
READY_TIMEOUT_SECONDS: Final[float] = 45.0
STOP_TIMEOUT_SECONDS: Final[float] = 10.0
_POLL_SECONDS: Final[float] = 0.2


class FakeModelProcessError(RuntimeError):
    """Raised when the detached fake model server cannot be managed."""


def _probe_host(host: str) -> str:
    """Return the address a local client should use for *host*."""
    return "127.0.0.1" if host in {"0.0.0.0", "", "::"} else host


def _get(host: str, port: int, path: str, *, timeout: float) -> tuple[int, bytes]:
    """GET a loopback path directly; http.client never consults system or env proxies."""
    connection = http.client.HTTPConnection(_probe_host(host), port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _healthy(host: str, port: int) -> bool:
    try:
        status, _ = _get(host, port, HEALTH_PATH, timeout=2)
    except OSError:
        return False
    return status == 200


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
    log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise FakeModelProcessError(
        f"fake model server not ready in {READY_TIMEOUT_SECONDS}s; log tail:\n{log_tail}"
    )


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
    status, body = _get(host, port, JOURNAL_PATH, timeout=10)
    if status != 200:
        raise FakeModelProcessError(f"fake model journal returned HTTP {status}")
    payload: object = json.loads(body.decode("utf-8"))
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
