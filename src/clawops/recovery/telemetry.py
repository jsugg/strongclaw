"""Recovery telemetry event helpers."""

from __future__ import annotations

from collections.abc import Mapping


def event_payload(event: str, fields: Mapping[str, object]) -> dict[str, object]:
    """Build one structured recovery telemetry payload."""
    return {"event": event, **dict(fields)}
