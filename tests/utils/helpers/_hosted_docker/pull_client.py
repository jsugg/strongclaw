"""Progress-aware `docker pull` client with stall detection and failure classification.

Without a TTY, `docker pull` prints only layer status changes, so a healthy multi-minute
layer download looks identical to a hung pull. The CLI client therefore runs `docker pull`
under a pseudo-terminal, which makes it emit per-layer byte counters, and treats only a
new layer status or an increased byte count as progress. A pull that makes no progress
for the stall window, or outlives its timeout, is terminated and reaped as a process group.
"""

from __future__ import annotations

import collections
import errno
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from tests.utils.helpers._fresh_host.budget import SYSTEM_CLOCK, Clock
from tests.utils.helpers._hosted_docker.process import terminate_process_group

type PullFailureClass = Literal[
    "stalled",
    "budget_exhausted",
    "registry_transient",
    "daemon_unavailable",
    "disk_full",
    "deterministic",
    "unknown",
]

STALL_TIMEOUT_SECONDS: Final[int] = 300
_OUTPUT_TAIL_LINES: Final[int] = 60
_ANSI_ESCAPE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_LAYER_LINE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<layer>[0-9a-f]{12}): (?P<status>[A-Za-z][A-Za-z ]*?)"
    r"(?:\s+\[[=> ]*\])?(?:\s+(?P<current>[\d.]+)\s*(?P<unit>[kMG]?B)/[\d.]+\s*[kMG]?B)?\s*$"
)
_DOWNLOADING: Final[str] = "Downloading"
_UNIT_BYTES: Final[Mapping[str, int]] = {"B": 1, "kB": 1_000, "MB": 1_000_000, "GB": 1_000_000_000}

# Ordered most specific first; the first matching class wins.
_FAILURE_MARKERS: Final[tuple[tuple[PullFailureClass, tuple[str, ...]], ...]] = (
    ("disk_full", ("no space left on device", "disk quota exceeded")),
    (
        "deterministic",
        (
            "unauthorized",
            "denied: ",
            "access denied",
            "manifest unknown",
            "no matching manifest",
            "invalid reference format",
            "repository does not exist",
            "not found: manifest",
            "pull access denied",
        ),
    ),
    (
        "daemon_unavailable",
        (
            "cannot connect to the docker daemon",
            "/run/containerd/containerd.sock",
            "error while dialing",
            "error during connect",
            "connection refused",
            "connection reset by peer",
            "unexpected eof",
        ),
    ),
    (
        "registry_transient",
        (
            "toomanyrequests",
            "429 too many requests",
            "500 internal server error",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
            "context deadline exceeded",
            "request canceled while waiting for connection",
            "timeout exceeded while awaiting headers",
            "i/o timeout",
            "tls handshake timeout",
            "temporary failure in name resolution",
            "no such host",
        ),
    ),
)
RETRYABLE_FAILURE_CLASSES: Final[frozenset[PullFailureClass]] = frozenset(
    {"stalled", "registry_transient", "daemon_unavailable", "unknown"}
)


def classify_pull_failure(output: str) -> PullFailureClass:
    """Classify a failed pull from its output; unknown failures stay retryable."""
    lowered = output.lower()
    for failure_class, markers in _FAILURE_MARKERS:
        if any(marker in lowered for marker in markers):
            return failure_class
    if "docker.sock" in lowered and "eof" in lowered:
        return "daemon_unavailable"
    return "unknown"


@dataclass(slots=True)
class PullOutcome:
    """Result of one pull attempt for one image."""

    image: str
    returncode: int
    duration_seconds: float
    output_tail: str
    failure_class: PullFailureClass | None = None
    seconds_since_progress: float = 0.0
    bytes_observed: int = 0

    @property
    def ok(self) -> bool:
        """Return whether the pull succeeded."""
        return self.returncode == 0


class DockerPullClient(Protocol):
    """Pulls one image within a timeout and a stall window."""

    def pull(self, image: str, *, timeout_seconds: float, stall_seconds: float) -> PullOutcome:
        """Pull *image*; never return later than *timeout_seconds* from the call."""
        ...


def _phase_bytes() -> dict[tuple[str, str], int]:
    return {}


def _seen_statuses() -> set[tuple[str, str]]:
    return set()


def _seen_lines() -> set[str]:
    return set()


@dataclass(slots=True)
class ProgressTracker:
    """Decide whether new pull output reflects real progress."""

    # Peak counters per (layer, status): extraction restarts below the downloaded size.
    phase_bytes: dict[tuple[str, str], int] = field(default_factory=_phase_bytes)
    statuses: set[tuple[str, str]] = field(default_factory=_seen_statuses)
    other_lines: set[str] = field(default_factory=_seen_lines)

    @property
    def bytes_observed(self) -> int:
        """Return the summed peak download counters across layers."""
        return sum(peak for (_, status), peak in self.phase_bytes.items() if status == _DOWNLOADING)

    def observe(self, line: str) -> bool:
        """Record one output line; return whether it shows progress."""
        text = line.strip()
        if not text:
            return False
        match = _LAYER_LINE.match(text)
        if match is None:
            if text in self.other_lines:
                return False
            self.other_lines.add(text)
            return True
        phase = (match["layer"], match["status"].strip())
        progressed = False
        if phase not in self.statuses:
            self.statuses.add(phase)
            progressed = True
        if match["current"] is not None:
            current = round(float(match["current"]) * _UNIT_BYTES[match["unit"]])
            if current > self.phase_bytes.get(phase, 0):
                self.phase_bytes[phase] = current
                progressed = True
        return progressed


def split_terminal_output(buffer: str) -> tuple[list[str], str]:
    """Split PTY output into complete lines (on CR or LF) plus an unterminated remainder."""
    cleaned = _ANSI_ESCAPE.sub("", buffer)
    parts = re.split(r"[\r\n]+", cleaned)
    return parts[:-1], parts[-1]


class CliDockerPullClient:
    """Run `docker pull` under a pseudo-terminal and supervise its progress."""

    def __init__(
        self,
        *,
        command: Sequence[str] = ("docker", "pull"),
        env: Mapping[str, str] | None = None,
        clock: Clock = SYSTEM_CLOCK,
        poll_seconds: float = 1.0,
    ) -> None:
        self._command = tuple(command)
        self._env = None if env is None else dict(env)
        self._clock = clock
        self._poll_seconds = poll_seconds

    def pull(self, image: str, *, timeout_seconds: float, stall_seconds: float) -> PullOutcome:
        """Pull *image*, terminating it on timeout or stall."""
        started = self._clock.monotonic()
        master_fd, slave_fd = pty.openpty()
        # A fresh PTY reports a 0x0 window; give docker room for full progress lines.
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        try:
            process = subprocess.Popen(
                [*self._command, image],
                stdin=subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                env=self._env,
                start_new_session=True,
            )
        except OSError as exc:
            os.close(master_fd)
            os.close(slave_fd)
            return PullOutcome(
                image=image,
                returncode=127,
                duration_seconds=0.0,
                output_tail=f"failed to start docker pull: {exc}",
                failure_class="daemon_unavailable",
            )
        os.close(slave_fd)
        tracker = ProgressTracker()
        tail: collections.deque[str] = collections.deque(maxlen=_OUTPUT_TAIL_LINES)
        pending = ""
        last_progress = started
        terminated_as: PullFailureClass | None = None
        try:
            while True:
                pending, eof, progressed = self._read_available(master_fd, pending, tracker, tail)
                now = self._clock.monotonic()
                if progressed:
                    last_progress = now
                if eof and process.poll() is not None:
                    break
                if now - started >= timeout_seconds:
                    terminated_as = "budget_exhausted"
                elif now - last_progress >= stall_seconds:
                    terminated_as = "stalled"
                if terminated_as is not None:
                    terminate_process_group(process)
                    break
        finally:
            os.close(master_fd)
        returncode = process.wait()
        finished = self._clock.monotonic()
        output_tail = "\n".join(tail)
        failure_class: PullFailureClass | None = terminated_as
        if terminated_as is not None:
            returncode = returncode or 1
            output_tail = (
                f"{output_tail}\n[hosted-docker] terminated docker pull ({terminated_as}) after "
                f"{finished - started:.0f}s, {finished - last_progress:.0f}s without progress"
            ).strip()
        elif returncode != 0:
            failure_class = classify_pull_failure(output_tail)
        return PullOutcome(
            image=image,
            returncode=returncode,
            duration_seconds=finished - started,
            output_tail=output_tail,
            failure_class=failure_class,
            seconds_since_progress=finished - last_progress,
            bytes_observed=tracker.bytes_observed,
        )

    def _read_available(
        self,
        master_fd: int,
        pending: str,
        tracker: ProgressTracker,
        tail: collections.deque[str],
    ) -> tuple[str, bool, bool]:
        """Read ready output; return the unterminated remainder, EOF, and whether it progressed."""
        readable, _, _ = select.select([master_fd], [], [], self._poll_seconds)
        if not readable:
            return pending, False, False
        try:
            chunk = os.read(master_fd, 65536)
        except OSError as exc:
            # Linux reports EIO on the master once every slave fd is closed.
            if exc.errno == errno.EIO:
                return pending, True, False
            raise
        if not chunk:
            return pending, True, False
        lines, remainder = split_terminal_output(pending + chunk.decode("utf-8", "replace"))
        progressed = False
        for line in lines:
            progressed = tracker.observe(line) or progressed
            if line.strip():
                tail.append(line.strip())
        return remainder, False, progressed
