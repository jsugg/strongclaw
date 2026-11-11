"""CI-only fault injection for proving fresh-host recovery paths on real runners.

A fault is requested through `FRESH_HOST_FAULT_INJECTION` and honored only on a manual
`workflow_dispatch` run off `main`; anywhere else the request is logged and ignored, so the
switch is inert on push, pull-request, schedule, and local runs. Each fault fires at most
once per scenario: a marker under the diagnostics directory records the shot, so a retried
step runs clean and the artifacts show when the fault fired.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.shell import (
    SIDECAR_EXPECTED_SERVICES,
    SIDECAR_HEALTHY_SERVICES,
    compose_probe_env,
    compose_service_entries,
    compose_service_health,
    compose_service_state,
    run_command,
)
from tests.utils.helpers._fresh_host.storage import log, now_iso

FAULT_INJECTION_ENV: Final[str] = "FRESH_HOST_FAULT_INJECTION"
FAULT_INJECTION_OFF: Final[str] = "none"
# Stops the OrbStack VM right before the macOS sidecars stack starts.
ORBSTACK_STOP_DURING_SIDECARS: Final[str] = "orbstack-stop-during-sidecars"
# Stops the OrbStack VM once `sidecars up` has started the stack and waits on its health.
ORBSTACK_STOP_DURING_SIDECARS_HEALTH: Final[str] = "orbstack-stop-during-sidecars-health"
KNOWN_FAULTS: Final[frozenset[str]] = frozenset(
    {ORBSTACK_STOP_DURING_SIDECARS, ORBSTACK_STOP_DURING_SIDECARS_HEALTH}
)
FAULT_INJECTION_EVENT: Final[str] = "workflow_dispatch"
PROTECTED_REF: Final[str] = "refs/heads/main"
_ORB_STOP_TIMEOUT_SECONDS: Final[int] = 120
_HEALTH_WAIT_POLL_SECONDS: Final[float] = 1.0
# `up -d` returns right after its containers start; a second sighting puts the stop inside
# the health wait that follows instead of racing the tail of `up`.
_HEALTH_WAIT_SIGHTINGS: Final[int] = 2
_HEALTH_WAIT_PROBE_TIMEOUT_SECONDS: Final[int] = 30
_WATCHER_JOIN_SECONDS: Final[float] = float(
    _ORB_STOP_TIMEOUT_SECONDS + _HEALTH_WAIT_PROBE_TIMEOUT_SECONDS
)


def requested_fault(environ: Mapping[str, str]) -> str | None:
    """Return the fault this run may inject, or `None` when injection is off or not allowed."""
    fault = environ.get(FAULT_INJECTION_ENV, "").strip()
    if not fault or fault == FAULT_INJECTION_OFF:
        return None
    if fault not in KNOWN_FAULTS:
        raise FreshHostError(
            f"{FAULT_INJECTION_ENV}={fault!r} is not one of {sorted(KNOWN_FAULTS)}"
        )
    event = environ.get("GITHUB_EVENT_NAME", "")
    ref = environ.get("GITHUB_REF", "")
    if event != FAULT_INJECTION_EVENT or ref == PROTECTED_REF:
        log(
            f"Ignoring {FAULT_INJECTION_ENV}={fault}: fault injection runs only on "
            f"{FAULT_INJECTION_EVENT} off {PROTECTED_REF} (event={event!r}, ref={ref!r})."
        )
        return None
    return fault


def _marker_path(context: FreshHostContext, fault: str) -> Path:
    """Return the one-shot marker that records when *fault* fired."""
    return Path(context.diagnostics_dir).resolve() / "fault-injection" / f"{fault}.json"


def _armed(context: FreshHostContext, fault: str, environ: Mapping[str, str] | None) -> bool:
    """Return whether *fault* is requested, allowed here, and has not fired yet."""
    if requested_fault(os.environ if environ is None else environ) != fault:
        return False
    return not _marker_path(context, fault).exists()


def _stop_orbstack(context: FreshHostContext, fault: str, *, when: str) -> None:
    """Record the one-shot marker for *fault*, then run `orb stop`."""
    marker = _marker_path(context, fault)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"fault": fault, "fired_at": now_iso(), "when": when}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"Fault injection {fault}: stopping OrbStack ({when}).")
    # The runner env keeps the real HOME, which `orb` resolves its control socket from.
    run_command(
        ["orb", "stop"],
        cwd=Path(context.repo_root).resolve(),
        env=dict(os.environ),
        timeout_seconds=_ORB_STOP_TIMEOUT_SECONDS,
    )


def inject_sidecars_runtime_loss(
    context: FreshHostContext, *, environ: Mapping[str, str] | None = None
) -> bool:
    """Run `orb stop` once when the sidecars runtime-loss fault is requested; return if it fired."""
    fault = ORBSTACK_STOP_DURING_SIDECARS
    if not _armed(context, fault, environ):
        return False
    _stop_orbstack(context, fault, when="before the sidecars stack starts")
    return True


def pending_sidecar_health(services: Mapping[str, dict[str, object]]) -> str | None:
    """Describe the sidecars still unhealthy once the whole stack runs; `None` otherwise."""
    if any(
        compose_service_state(services.get(name, {})) != "running"
        for name in SIDECAR_EXPECTED_SERVICES
    ):
        return None
    pending = [
        f"{name}={compose_service_health(services[name]) or 'unknown'}"
        for name in SIDECAR_HEALTHY_SERVICES
        if compose_service_health(services[name]) != "healthy"
    ]
    return ", ".join(pending) or None


def _stop_orbstack_in_health_wait(
    context: FreshHostContext,
    *,
    fault: str,
    compose_file: Path,
    env: dict[str, str],
    stop: threading.Event,
) -> None:
    """Poll the sidecars stack and stop OrbStack once it is up and waiting on health."""
    cwd = Path(context.repo_root).resolve() / "platform" / "compose"
    sightings = 0
    while not stop.wait(_HEALTH_WAIT_POLL_SECONDS):
        try:
            services = compose_service_entries(
                compose_file,
                cwd=cwd,
                env=env,
                timeout_seconds=_HEALTH_WAIT_PROBE_TIMEOUT_SECONDS,
            )
        except FreshHostError:
            sightings = 0
            continue
        if stop.is_set():
            return
        pending = pending_sidecar_health(services)
        sightings = sightings + 1 if pending is not None else 0
        if sightings < _HEALTH_WAIT_SIGHTINGS:
            continue
        try:
            _stop_orbstack(context, fault, when=f"inside the sidecars health wait: {pending}")
        except FreshHostError as exc:
            log(f"Fault injection {fault}: `orb stop` failed: {exc}")
        return


@contextmanager
def orbstack_stop_during_sidecars_health(
    context: FreshHostContext,
    *,
    compose_file: Path,
    environ: Mapping[str, str] | None = None,
) -> Generator[None]:
    """While the body runs `sidecars up`, stop OrbStack once inside its health wait.

    Inert unless the health-wait fault is requested and allowed. On exit the watcher is
    joined, so a recovery that follows never races an `orb stop` still in flight.
    """
    fault = ORBSTACK_STOP_DURING_SIDECARS_HEALTH
    if not _armed(context, fault, environ):
        yield
        return
    stop = threading.Event()
    watcher = threading.Thread(
        target=_stop_orbstack_in_health_wait,
        kwargs={
            "context": context,
            "fault": fault,
            "compose_file": compose_file,
            "env": compose_probe_env(context, compose_file=compose_file, repo_local_state=True),
            "stop": stop,
        },
        name=f"fault-injection-{fault}",
        daemon=True,
    )
    log(f"Fault injection {fault}: armed; watching for the sidecars health wait.")
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join(timeout=_WATCHER_JOIN_SECONDS)
        if not _marker_path(context, fault).exists():
            log(f"Fault injection {fault} did not fire: no sidecars health wait was observed.")
