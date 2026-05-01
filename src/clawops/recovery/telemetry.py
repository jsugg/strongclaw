"""Recovery telemetry event helpers."""

from __future__ import annotations

from collections.abc import Mapping

from clawops.observability import TelemetryValue


def event_payload(event: str, fields: Mapping[str, TelemetryValue]) -> dict[str, TelemetryValue]:
    """Build one structured recovery telemetry payload."""
    return {"event": event, **dict(fields)}
