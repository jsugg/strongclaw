"""Gateway and sidecar runtime control for feature-verification lanes.

On lanes that activate host services the gateway belongs to systemd/launchd and the harness
only restarts it through the service manager. When activation is off (or failed, which the
setup feature reports on its own) the harness starts ``clawops ops gateway start`` itself, so
the remaining features still get a real gateway rather than cascading into failures.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from tests.utils.helpers._feature_verification.host import FeatureHost

type GatewayMode = Literal["systemd", "launchd", "harness"]

SYSTEMD_GATEWAY_UNIT: Final[str] = "openclaw-gateway.service"
LAUNCHD_GATEWAY_LABEL: Final[str] = "ai.openclaw.gateway"
DEFAULT_GATEWAY_PORT: Final[int] = 18789
GATEWAY_READY_TIMEOUT_SECONDS: Final[float] = 150.0
_POLL_SECONDS: Final[float] = 2.0
_STOP_TIMEOUT_SECONDS: Final[float] = 20.0
_LOG_TAIL_CHARS: Final[int] = 2_000_000
# OpenClaw writes its rolling file log here regardless of TMPDIR.
OPENCLAW_TMP_LOG_DIR: Final[str] = "/tmp/openclaw"


def tcp_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    """Return whether *host*:*port* accepts TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def gateway_port(host: FeatureHost) -> int:
    """Return the gateway port from the rendered config."""
    gateway = host.rendered_config().get("gateway")
    if isinstance(gateway, dict):
        port = cast(dict[str, object], gateway).get("port")
        if isinstance(port, int):
            return port
    return DEFAULT_GATEWAY_PORT


def gateway_status(host: FeatureHost) -> dict[str, object]:
    """Return ``openclaw gateway status --json``."""
    payload = host.openclaw("gateway", "status", "--json", timeout=120).require_ok().json()
    assert isinstance(payload, dict), payload
    return cast(dict[str, object], payload)


def gateway_rpc_ok(host: FeatureHost) -> bool:
    """Return whether the gateway answers RPC."""
    outcome = host.openclaw("gateway", "status", "--json", timeout=120)
    if not outcome.ok:
        return False
    try:
        payload = outcome.json()
    except AssertionError:
        return False
    rpc = cast(dict[str, object], payload).get("rpc") if isinstance(payload, dict) else None
    return isinstance(rpc, dict) and cast(dict[str, object], rpc).get("ok") is True


def plugin_statuses(host: FeatureHost) -> dict[str, str]:
    """Return ``{plugin id: status}`` from ``openclaw plugins list --json``."""
    payload = host.openclaw("plugins", "list", "--json", timeout=300).require_ok().json()
    assert isinstance(payload, dict), payload
    plugins = cast(dict[str, object], payload).get("plugins")
    assert isinstance(plugins, list), payload
    statuses: dict[str, str] = {}
    for raw in cast(list[object], plugins):
        if isinstance(raw, dict):
            entry = cast(dict[str, object], raw)
            statuses[str(entry.get("id"))] = str(entry.get("status"))
    return statuses


def service_active(host: FeatureHost) -> bool:
    """Return whether the service manager reports the gateway as running."""
    if host.lane.platform == "linux":
        return host.run(
            ["systemctl", "--user", "is-active", "--quiet", SYSTEMD_GATEWAY_UNIT], timeout=30
        ).ok
    outcome = host.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_GATEWAY_LABEL}"], timeout=30
    )
    return outcome.ok and "state = running" in outcome.stdout


@dataclass(slots=True)
class GatewayController:
    """Own the gateway lifecycle for one lane session."""

    host: FeatureHost
    _process: subprocess.Popen[bytes] | None = None
    _log_path: Path | None = None

    @property
    def mode(self) -> GatewayMode:
        """Return who supervises the gateway."""
        if self._process is not None or not self.host.lane.activate_services:
            return "harness"
        return "systemd" if self.host.lane.platform == "linux" else "launchd"

    @property
    def log_path(self) -> Path | None:
        """Return the harness-managed gateway log, when the harness started it."""
        return self._log_path

    def log_text(self) -> str:
        """Return recent gateway output from every place the active supervisor writes it."""
        chunks: list[str] = []
        if self._log_path is not None and self._log_path.is_file():
            chunks.append(self._log_path.read_text(encoding="utf-8", errors="replace"))
        if self.host.lane.platform == "linux":
            journal = self.host.run(
                ["journalctl", "--user", "-u", SYSTEMD_GATEWAY_UNIT, "--no-pager", "-n", "5000"],
                timeout=60,
            )
            chunks.append(journal.stdout)
        state_logs = self.host.home / ".openclaw" / "logs"
        file_logs = [
            *sorted(state_logs.glob("*gateway*.log")),
            *sorted(Path(OPENCLAW_TMP_LOG_DIR).glob("openclaw-*.log")),
        ]
        for log_file in file_logs:
            chunks.append(log_file.read_text(encoding="utf-8", errors="replace")[-_LOG_TAIL_CHARS:])
        return self.host.redact("\n".join(chunks))

    def wait_ready(self, timeout: float = GATEWAY_READY_TIMEOUT_SECONDS) -> None:
        """Block until the gateway port is open and RPC answers."""
        port = gateway_port(self.host)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise AssertionError(
                    f"harness gateway exited with {self._process.returncode}; see {self._log_path}"
                )
            if tcp_open(port) and gateway_rpc_ok(self.host):
                return
            time.sleep(_POLL_SECONDS)
        raise AssertionError(f"gateway on port {port} not ready within {timeout:.0f}s")

    def _start_harness_process(self) -> None:
        self._log_path = self.host.work_dir / f"gateway-{time.time_ns()}.log"
        with self._log_path.open("ab") as log_handle:
            self._process = subprocess.Popen(  # noqa: S603 - fixed clawops argv
                [
                    str(self.host.python),
                    "-m",
                    "clawops",
                    "ops",
                    "--asset-root",
                    ".",
                    "gateway",
                    "start",
                ],
                cwd=self.host.repo_root,
                env=self.host.product_env(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def ensure_running(self) -> GatewayMode:
        """Make sure a gateway answers; start a harness gateway when none is supervised."""
        if gateway_rpc_ok(self.host):
            return self.mode
        if self.mode != "harness" and service_active(self.host):
            self.wait_ready()
            return self.mode
        if self._process is None:
            self._start_harness_process()
        self.wait_ready()
        return self.mode

    def restart(self) -> None:
        """Restart the gateway so it reloads the rendered config."""
        if self.mode == "systemd":
            self.host.run(
                ["systemctl", "--user", "restart", SYSTEMD_GATEWAY_UNIT], timeout=60
            ).require_ok()
        elif self.mode == "launchd":
            self.host.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_GATEWAY_LABEL}"],
                timeout=60,
            ).require_ok()
        else:
            self.stop()
            self._start_harness_process()
        time.sleep(_POLL_SECONDS)
        self.wait_ready()

    def stop(self) -> None:
        """Stop a harness-started gateway (its whole process group)."""
        process = self._process
        if process is None:
            return
        self._process = None
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)


def sidecars_ready(host: FeatureHost) -> bool:
    """Return whether ``clawops ops status`` reports every required sidecar ready."""
    outcome = host.clawops("ops", "--asset-root", ".", "status", timeout=300)
    try:
        payload = outcome.json()
    except AssertionError:
        return False
    if not isinstance(payload, dict):
        return False
    readiness = cast(dict[str, object], payload).get("readiness")
    return (
        isinstance(readiness, dict)
        and cast(dict[str, object], readiness).get("requiredReady") is True
    )


def ensure_sidecars(host: FeatureHost) -> str:
    """Bring the sidecar stack up with ``clawops ops sidecars up`` when no service did."""
    if sidecars_ready(host):
        return "ready"
    host.clawops("ops", "--asset-root", ".", "sidecars", "up", timeout=1800).require_ok()
    return "started-by-harness"
