"""Checkpoint metadata contracts for recovery surfaces."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointRecord:
    """Minimal checkpoint metadata record."""

    checkpoint_id: str
    scope: str
    created_at_ms: int
