"""Unit coverage for stall-aware Docker pulls and their classified retry policy."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.utils.helpers import hosted_docker
from tests.utils.helpers._fresh_host.budget import PhaseDeadline
from tests.utils.helpers._hosted_docker.events import PullEventLog, read_events
from tests.utils.helpers._hosted_docker.models import MAX_STALL_RETRIES
from tests.utils.helpers._hosted_docker.pull_client import (
    CliDockerPullClient,
    ProgressTracker,
    PullFailureClass,
    PullOutcome,
    classify_pull_failure,
    split_terminal_output,
)
from tests.utils.helpers.clock import FakeClock

_FAKE_DOCKER = """
import os, subprocess, sys, time

mode = sys.argv[-1]
layer = "0123456789ab"
if mode == "healthy":
    for step in range(1, 9):
        print(f"{layer}: Downloading [==>   ]  {step}.0MB/9.0MB", flush=True)
        time.sleep(0.2)
    print(f"{layer}: Pull complete", flush=True)
elif mode == "silent":
    print(f"{layer}: Pulling fs layer", flush=True)
    time.sleep(30)
elif mode == "repeating":
    for _ in range(300):
        print(f"{layer}: Downloading [==>   ]  1.0MB/9.0MB", flush=True)
        time.sleep(0.05)
elif mode == "grandchild":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid_file = os.environ["FAKE_DOCKER_PID_FILE"]
    with open(pid_file + ".tmp", "w") as handle:
        handle.write(str(child.pid))
    # Publish atomically and before any output, so the file's presence means a complete pid.
    os.replace(pid_file + ".tmp", pid_file)
    print(f"{layer}: Pulling fs layer", flush=True)
    time.sleep(60)
elif mode == "missing":
    print("Error response from daemon: manifest unknown", flush=True)
    sys.exit(1)
"""


@pytest.fixture
def fake_docker(tmp_path: Path) -> Path:
    script = tmp_path / "fake_docker.py"
    script.write_text(_FAKE_DOCKER, encoding="utf-8")
    return script


def _cli_client(script: Path, *, env: dict[str, str] | None = None) -> CliDockerPullClient:
    return CliDockerPullClient(command=(sys.executable, str(script)), env=env, poll_seconds=0.05)


class _FileGatedClock:
    """Monotonic time that stands still until *gate* exists or a real-time deadline passes.

    The pull client's stall window starts with the pull, so a slow interpreter start under
    load would otherwise be killed before the fake docker publishes its grandchild pid.
    """

    def __init__(self, gate: Path, *, max_wait_seconds: float) -> None:
        self._gate = gate
        self._give_up_at = time.monotonic() + max_wait_seconds
        self._frozen_at = time.monotonic()
        self._opened_at: float | None = None

    def time(self) -> float:
        """Return wall-clock seconds since the epoch."""
        return time.time()

    def monotonic(self) -> float:
        """Return frozen time until the gate opens, then advance in real time."""
        now = time.monotonic()
        if self._opened_at is None:
            if not self._gate.exists() and now < self._give_up_at:
                return self._frozen_at
            self._opened_at = now
        return self._frozen_at + (now - self._opened_at)

    def sleep(self, seconds: float) -> None:
        """Block for *seconds*."""
        time.sleep(seconds)


def _process_gone(pid: int) -> bool:
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return status.stdout.strip() in ("", "Z") or status.stdout.strip().startswith("Z")


def test_cli_client_lets_a_slow_but_progressing_pull_finish(fake_docker: Path) -> None:
    outcome = _cli_client(fake_docker).pull("healthy", timeout_seconds=20, stall_seconds=0.8)

    assert outcome.ok
    assert outcome.duration_seconds > 0.8
    assert outcome.bytes_observed == 8_000_000


def test_cli_client_terminates_a_pull_without_progress(fake_docker: Path) -> None:
    started = time.monotonic()

    outcome = _cli_client(fake_docker).pull("silent", timeout_seconds=20, stall_seconds=0.5)

    assert outcome.failure_class == "stalled"
    assert not outcome.ok
    assert time.monotonic() - started < 10
    assert "terminated docker pull (stalled)" in outcome.output_tail


def test_cli_client_does_not_count_repeated_frames_as_progress(fake_docker: Path) -> None:
    outcome = _cli_client(fake_docker).pull("repeating", timeout_seconds=20, stall_seconds=0.5)

    assert outcome.failure_class == "stalled"


def test_cli_client_stops_at_its_timeout_even_while_progressing(fake_docker: Path) -> None:
    outcome = _cli_client(fake_docker).pull("healthy", timeout_seconds=0.5, stall_seconds=10)

    assert outcome.failure_class == "budget_exhausted"
    assert not outcome.ok


def test_cli_client_kills_and_reaps_the_whole_process_group(
    fake_docker: Path, tmp_path: Path
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    client = CliDockerPullClient(
        command=(sys.executable, str(fake_docker)),
        env={"FAKE_DOCKER_PID_FILE": str(pid_file), "PATH": ""},
        clock=_FileGatedClock(pid_file, max_wait_seconds=10),
        poll_seconds=0.05,
    )

    outcome = client.pull("grandchild", timeout_seconds=20, stall_seconds=0.5)

    assert pid_file.is_file(), "fake docker never published its grandchild pid within 10s"
    assert outcome.failure_class == "stalled"
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while not _process_gone(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _process_gone(grandchild)


def test_cli_client_classifies_a_failed_pull(fake_docker: Path) -> None:
    outcome = _cli_client(fake_docker).pull("missing", timeout_seconds=20, stall_seconds=5)

    assert outcome.returncode == 1
    assert outcome.failure_class == "deterministic"


def test_progress_tracker_counts_only_real_progress() -> None:
    tracker = ProgressTracker()

    assert tracker.observe("0123456789ab: Pulling fs layer")
    assert not tracker.observe("0123456789ab: Pulling fs layer")
    assert tracker.observe("0123456789ab: Downloading [=>  ]  1.5MB/9MB")
    assert not tracker.observe("0123456789ab: Downloading [=>  ]  1.5MB/9MB")
    assert not tracker.observe("0123456789ab: Downloading [>   ]  1.2MB/9MB")
    assert tracker.observe("0123456789ab: Downloading [==> ]  2.1MB/9MB")
    assert tracker.observe("Digest: sha256:abc")
    assert not tracker.observe("Digest: sha256:abc")
    assert not tracker.observe("   ")
    assert tracker.bytes_observed == 2_100_000


def test_progress_tracker_counts_extraction_after_a_completed_download() -> None:
    """Regression: extraction bytes restart below the downloaded size and still progress."""
    tracker = ProgressTracker()

    assert tracker.observe("f969fa5e591f: Downloading [=====>  ]  262.9MB/262.9MB")
    assert tracker.observe("f969fa5e591f: Download complete")
    assert tracker.observe("f969fa5e591f: Extracting [>   ]  126.5MB/262.9MB")
    assert tracker.observe("f969fa5e591f: Extracting [=>  ]  127MB/262.9MB")
    assert not tracker.observe("f969fa5e591f: Extracting [=>  ]  127MB/262.9MB")
    assert tracker.bytes_observed == 262_900_000


def test_split_terminal_output_strips_cursor_control_and_keeps_partial_lines() -> None:
    lines, remainder = split_terminal_output("\x1b[1A\x1b[2Kabc: Waiting\r\ndef: Downl")

    assert lines == ["abc: Waiting"]
    assert remainder == "def: Downl"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("write /var/lib/docker/tmp: no space left on device", "disk_full"),
        ("Error response from daemon: manifest unknown", "deterministic"),
        ("pull access denied for x, repository does not exist", "deterministic"),
        ("unauthorized: authentication required", "deterministic"),
        ("invalid reference format", "deterministic"),
        ("toomanyrequests: You have reached your pull rate limit", "registry_transient"),
        ("received unexpected HTTP status: 503 Service Unavailable", "registry_transient"),
        ("context deadline exceeded", "registry_transient"),
        ("Cannot connect to the Docker daemon at unix:///x.sock", "daemon_unavailable"),
        ("error during connect: EOF on docker.sock", "daemon_unavailable"),
        ("x509: certificate signed by unknown authority", "unknown"),
    ],
)
def test_classify_pull_failure(output: str, expected: PullFailureClass) -> None:
    assert classify_pull_failure(output) == expected


class _ScriptedClient:
    """Fake client that replays per-image outcomes and records its calls."""

    def __init__(
        self,
        script: dict[str, Sequence[PullFailureClass | None]],
        *,
        on_pull: Callable[[str], None] | None = None,
    ) -> None:
        self._script = {image: list(outcomes) for image, outcomes in script.items()}
        self._on_pull = on_pull
        self.calls: list[tuple[str, float, float]] = []

    def pull(self, image: str, *, timeout_seconds: float, stall_seconds: float) -> PullOutcome:
        self.calls.append((image, timeout_seconds, stall_seconds))
        if self._on_pull is not None:
            self._on_pull(image)
        failure_class = self._script[image].pop(0)
        return PullOutcome(
            image=image,
            returncode=0 if failure_class is None else 1,
            duration_seconds=1.0,
            output_tail="" if failure_class is None else f"fake {failure_class}",
            failure_class=failure_class,
        )


@pytest.mark.parametrize("terminal_class", ["deterministic", "disk_full"])
def test_pull_images_does_not_retry_non_transient_failures(
    terminal_class: PullFailureClass,
) -> None:
    client = _ScriptedClient({"postgres:16": [terminal_class]})

    report = hosted_docker.pull_images(
        ["postgres:16"], parallelism=1, max_attempts=5, client=client, clock=FakeClock()
    )

    assert report.exit_code == 1
    assert report.failure_class == terminal_class
    assert report.attempt_count == 1
    assert len(client.calls) == 1


def test_pull_images_caps_stalled_retries() -> None:
    stalls: list[PullFailureClass | None] = ["stalled"] * 5
    client = _ScriptedClient({"browserlab:1": stalls})
    clock = FakeClock()

    report = hosted_docker.pull_images(
        ["browserlab:1"],
        parallelism=1,
        max_attempts=5,
        client=client,
        clock=clock,
        jitter=lambda cap: 0.0,
    )

    assert report.failure_class == "stalled"
    assert len(client.calls) == MAX_STALL_RETRIES + 1
    assert client.calls[0][2] == hosted_docker.STALL_TIMEOUT_SECONDS


def test_pull_images_retries_transient_failures_with_jittered_backoff() -> None:
    client = _ScriptedClient({"qdrant:1": ["registry_transient", "registry_transient", None]})
    clock = FakeClock()
    caps: list[float] = []

    def half_jitter(cap: float) -> float:
        caps.append(cap)
        return cap / 2

    report = hosted_docker.pull_images(
        ["qdrant:1"],
        parallelism=1,
        max_attempts=5,
        client=client,
        clock=clock,
        jitter=half_jitter,
    )

    assert report.exit_code == 0
    assert caps == [10, 20]
    assert clock.sleeps == [5.0, 10.0]


def test_pull_images_never_starts_queued_pulls_after_the_budget_is_gone(tmp_path: Path) -> None:
    clock = FakeClock()
    deadline = PhaseDeadline.after(300, clock=clock)
    client = _ScriptedClient(
        {"first:1": [None], "second:1": [None]},
        on_pull=lambda image: clock.advance(290),
    )
    events = PullEventLog(tmp_path / "events.jsonl")

    report = hosted_docker.pull_images(
        ["first:1", "second:1"],
        parallelism=1,
        max_attempts=5,
        client=client,
        clock=clock,
        deadline=deadline,
        event_log=events,
    )

    assert [call[0] for call in client.calls] == ["first:1"]
    assert client.calls[0][1] == 300
    assert report.pulled_images == ["first:1"]
    assert report.failure_class == "budget_exhausted"
    results = [event for event in read_events(events.path) if event["event"] == "image_result"]
    assert [(event["image"], event["failure_class"]) for event in results] == [
        ("first:1", None),
        ("second:1", "budget_exhausted"),
    ]
    assert [record.image for record in report.attempts] == ["first:1", "second:1"]
