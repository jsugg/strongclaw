"""JSON persistence and formatting helpers for fresh-host reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tests.utils.helpers._fresh_host.models import (
    LOG_PREFIX,
    SCENARIO_SPECS,
    FreshHostContext,
    FreshHostError,
    FreshHostReport,
    PhaseResult,
    ScenarioSpec,
)


def log(message: str) -> None:
    """Emit one CI-friendly log line."""
    print(f"{LOG_PREFIX} {message}", flush=True)


def now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC).isoformat()


def require_scenario(scenario_id: str) -> ScenarioSpec:
    """Resolve one known scenario spec."""
    try:
        return SCENARIO_SPECS[scenario_id]  # type: ignore[index]
    except KeyError as exc:
        supported = ", ".join(sorted(SCENARIO_SPECS))
        raise FreshHostError(
            f"Unsupported scenario '{scenario_id}'. Expected one of: {supported}."
        ) from exc


def repo_root(path_text: str) -> Path:
    """Resolve the repository root path."""
    return Path(path_text).expanduser().resolve()


def context_path(path_text: str) -> Path:
    """Resolve one context/report path."""
    return Path(path_text).expanduser().resolve()


def write_json(payload: object, path: Path) -> None:
    """Write one JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    """Read one JSON payload."""
    if not path.is_file():
        raise FreshHostError(f"expected JSON file but found {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_github_env(assignments: dict[str, str], github_env_file: Path | None) -> None:
    """Append environment exports for downstream workflow steps."""
    if github_env_file is None:
        return
    lines = [f"{key}={value}" for key, value in assignments.items()]
    with github_env_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def format_duration(seconds: float) -> str:
    """Render one duration in minutes and seconds."""
    total = int(round(seconds))
    minutes, remaining = divmod(total, 60)
    return f"{minutes}m {remaining}s"


def load_context(path: Path) -> FreshHostContext:
    """Load one scenario context from disk."""
    return FreshHostContext(**read_json(path))


def load_report(path: Path) -> FreshHostReport:
    """Load one scenario report from disk."""
    payload = read_json(path)
    raw_phases = payload.pop("phases", [])
    phases = [PhaseResult(**raw_phase) for raw_phase in raw_phases if isinstance(raw_phase, dict)]
    return FreshHostReport(phases=phases, **payload)


def write_report(report: FreshHostReport, path: Path) -> None:
    """Persist one scenario report to disk."""
    report.updated_at = now_iso()
    write_json(asdict(report), path)
