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
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.models import FreshHostContext, FreshHostError
from tests.utils.helpers._fresh_host.shell import run_command
from tests.utils.helpers._fresh_host.storage import log, now_iso

FAULT_INJECTION_ENV: Final[str] = "FRESH_HOST_FAULT_INJECTION"
FAULT_INJECTION_OFF: Final[str] = "none"
# Stops the OrbStack VM right before the macOS sidecars stack starts.
ORBSTACK_STOP_DURING_SIDECARS: Final[str] = "orbstack-stop-during-sidecars"
KNOWN_FAULTS: Final[frozenset[str]] = frozenset({ORBSTACK_STOP_DURING_SIDECARS})
FAULT_INJECTION_EVENT: Final[str] = "workflow_dispatch"
PROTECTED_REF: Final[str] = "refs/heads/main"
_ORB_STOP_TIMEOUT_SECONDS: Final[int] = 120


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


def inject_sidecars_runtime_loss(
    context: FreshHostContext, *, environ: Mapping[str, str] | None = None
) -> bool:
    """Run `orb stop` once when the sidecars runtime-loss fault is requested; return if it fired."""
    fault = ORBSTACK_STOP_DURING_SIDECARS
    if requested_fault(os.environ if environ is None else environ) != fault:
        return False
    marker = _marker_path(context, fault)
    if marker.exists():
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"fault": fault, "fired_at": now_iso()}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"Fault injection {fault}: stopping OrbStack.")
    # The runner env keeps the real HOME, which `orb` resolves its control socket from.
    run_command(
        ["orb", "stop"],
        cwd=Path(context.repo_root).resolve(),
        env=dict(os.environ),
        timeout_seconds=_ORB_STOP_TIMEOUT_SECONDS,
    )
    return True
