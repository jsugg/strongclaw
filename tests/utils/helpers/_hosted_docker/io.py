"""Logging and file I/O helpers for hosted Docker CI."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from tests.utils.helpers._hosted_docker.models import LOG_PREFIX


def log(message: str) -> None:
    """Emit one CI-friendly log line."""
    print(f"{LOG_PREFIX} {message}", flush=True)


def now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC).isoformat()


def write_json(payload: object, path: Path) -> None:
    """Persist one JSON payload atomically, so a killed step never leaves a torn report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        staging.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def write_github_env(assignments: dict[str, str], github_env_file: Path | None) -> None:
    """Append one batch of exports to GITHUB_ENV."""
    if github_env_file is None:
        return
    with github_env_file.open("a", encoding="utf-8") as handle:
        for key, value in assignments.items():
            handle.write(f"{key}={value}\n")
