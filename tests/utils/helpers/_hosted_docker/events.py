"""Append-only JSONL event log so a hard-killed pull phase still leaves evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from tests.utils.helpers._hosted_docker.io import now_iso


class PullEventLog:
    """Write one JSON object per line, flushing and closing after every event."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: object) -> None:
        """Append one event record."""
        record: dict[str, object] = {"event": event, "at": now_iso(), **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def read_events(path: Path) -> list[dict[str, object]]:
    """Read every complete event record, ignoring a torn trailing line."""
    if not path.is_file():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items = cast(dict[object, object], payload).items()
            events.append({str(key): value for key, value in items})
    return events
