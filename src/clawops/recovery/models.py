"""Data models for the recovery subsystem."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Literal

type RecoveryProfile = Literal[
    "control-plane",
    "devflow-checkpoint",
    "hypermemory-fast",
    "full-data-plane",
]


@dataclasses.dataclass(frozen=True, slots=True)
class BackupPlan:
    """Deterministic backup plan payload."""

    profile: RecoveryProfile
    include_roots: tuple[pathlib.Path, ...]
    exclude_roots: tuple[pathlib.Path, ...]
    backend_candidates: tuple[str, ...]
    estimated_bytes: int
    estimated_file_count: int
    retention: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        """Render a JSON-safe payload."""
        return {
            "profile": self.profile,
            "include_roots": [path.as_posix() for path in self.include_roots],
            "exclude_roots": [path.as_posix() for path in self.exclude_roots],
            "backend_candidates": list(self.backend_candidates),
            "estimated_bytes": self.estimated_bytes,
            "estimated_file_count": self.estimated_file_count,
            "retention": dict(self.retention),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class BackupCreateExecution:
    """Result of backup orchestration."""

    plan: BackupPlan
    dry_run: bool
    archive_path: pathlib.Path | None = None
    mode: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
