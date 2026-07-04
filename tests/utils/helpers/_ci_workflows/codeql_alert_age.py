"""Build a report-only age inventory for open high/critical CodeQL alerts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from tests.utils.helpers._ci_workflows.common import CiWorkflowError

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TRACKED_SEVERITIES = frozenset({"critical", "high"})
_SEVERITY_ORDER = {"critical": 0, "high": 1}


@dataclass(frozen=True, slots=True)
class CodeqlAlertAge:
    """One open high/critical CodeQL alert and its age."""

    number: int
    rule_id: str
    severity: str
    created_at: datetime
    html_url: str
    age_days: int
    stale: bool

    def to_json(self) -> dict[str, object]:
        """Return a JSON-serializable alert record."""
        return {
            "number": self.number,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "html_url": self.html_url,
            "age_days": self.age_days,
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class CodeqlAlertAgeReport:
    """Report-only CodeQL alert-age inventory."""

    repository: str
    generated_at: datetime
    stale_days: int
    alerts: tuple[CodeqlAlertAge, ...]

    @property
    def stale_count(self) -> int:
        """Return the number of tracked alerts at or beyond the age threshold."""
        return sum(alert.stale for alert in self.alerts)

    def to_json(self) -> dict[str, object]:
        """Return the versioned JSON report payload."""
        return {
            "schema_version": 1,
            "repository": self.repository,
            "generated_at": self.generated_at.isoformat().replace("+00:00", "Z"),
            "policy": {
                "stale_days": self.stale_days,
                "tracked_severities": sorted(_TRACKED_SEVERITIES),
                "enforcement": "report-only",
            },
            "summary": {
                "open_high_critical": len(self.alerts),
                "stale_high_critical": self.stale_count,
            },
            "alerts": [alert.to_json() for alert in self.alerts],
        }


def load_codeql_alert_payload(path: Path) -> object:
    """Load a GitHub code-scanning API payload from disk."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CiWorkflowError(f"unable to read CodeQL alert payload {path}: {exc}") from exc


def build_codeql_alert_age_report(
    payload: object,
    *,
    repository: str,
    stale_days: int = 30,
    generated_at: datetime | None = None,
) -> CodeqlAlertAgeReport:
    """Validate GitHub API data and build a high/critical alert-age report."""
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise CiWorkflowError(f"invalid GitHub repository: {repository!r}")
    if stale_days < 1:
        raise CiWorkflowError("stale_days must be at least 1")

    report_time = generated_at or datetime.now(timezone.utc)
    if report_time.tzinfo is None:
        raise CiWorkflowError("generated_at must be timezone-aware")
    report_time = report_time.astimezone(timezone.utc)

    alerts: list[CodeqlAlertAge] = []
    for raw_alert in _flatten_api_pages(payload):
        alert = _parse_alert(
            raw_alert,
            generated_at=report_time,
            stale_days=stale_days,
        )
        if alert is not None:
            alerts.append(alert)

    alerts.sort(
        key=lambda alert: (
            not alert.stale,
            _SEVERITY_ORDER[alert.severity],
            -alert.age_days,
            alert.number,
        )
    )
    return CodeqlAlertAgeReport(
        repository=repository,
        generated_at=report_time,
        stale_days=stale_days,
        alerts=tuple(alerts),
    )


def render_codeql_alert_age_markdown(report: CodeqlAlertAgeReport) -> str:
    """Render a concise GitHub step-summary report."""
    lines = [
        "## CodeQL alert-age report",
        "",
        "Report-only; this workflow does not change alert state or merge policy.",
        "",
        f"- Open high/critical alerts: **{len(report.alerts)}**",
        f"- At least {report.stale_days} days old: **{report.stale_count}**",
        "",
    ]
    if not report.alerts:
        lines.append("No open high/critical alerts.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Alert | Severity | Rule | Age | Stale |",
            "|---:|---|---|---:|---|",
        ]
    )
    for alert in report.alerts:
        rule_id = alert.rule_id.replace("|", "\\|")
        lines.append(
            f"| [#{alert.number}]({alert.html_url}) | {alert.severity} | "
            f"`{rule_id}` | {alert.age_days} days | {'yes' if alert.stale else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def write_codeql_alert_age_report(
    report: CodeqlAlertAgeReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    """Write JSON and Markdown report artifacts."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_codeql_alert_age_markdown(report),
        encoding="utf-8",
    )


def _flatten_api_pages(payload: object) -> list[object]:
    if not isinstance(payload, list):
        raise CiWorkflowError("CodeQL alert payload must be a JSON array")

    items = cast(list[object], payload)
    if items and all(isinstance(item, list) for item in items):
        flattened: list[object] = []
        for page in items:
            flattened.extend(cast(list[object], page))
        return flattened
    return items


def _parse_alert(
    value: object,
    *,
    generated_at: datetime,
    stale_days: int,
) -> CodeqlAlertAge | None:
    alert = _string_key_mapping(value, "alert")
    state = alert.get("state")
    if state != "open":
        return None

    tool = _string_key_mapping(alert.get("tool"), "alert.tool")
    if tool.get("name") != "CodeQL":
        return None

    rule = _string_key_mapping(alert.get("rule"), "alert.rule")
    severity_value = rule.get("security_severity_level")
    if not isinstance(severity_value, str):
        return None
    severity = severity_value.lower()
    if severity not in _TRACKED_SEVERITIES:
        return None

    number = alert.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise CiWorkflowError("alert.number must be a positive integer")
    rule_id = _required_non_empty_string(rule.get("id"), "alert.rule.id")
    html_url = _required_non_empty_string(alert.get("html_url"), "alert.html_url")
    created_at = _parse_timestamp(alert.get("created_at"), "alert.created_at")
    age_days = max(0, int((generated_at - created_at).total_seconds() // 86_400))
    return CodeqlAlertAge(
        number=number,
        rule_id=rule_id,
        severity=severity,
        created_at=created_at,
        html_url=html_url,
        age_days=age_days,
        stale=age_days >= stale_days,
    )


def _string_key_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CiWorkflowError(f"{name} must be a JSON object")

    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise CiWorkflowError(f"{name} contains a non-string key")
        result[key] = item
    return result


def _required_non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CiWorkflowError(f"{name} must be a non-empty string")
    return value.strip()


def _parse_timestamp(value: object, name: str) -> datetime:
    raw = _required_non_empty_string(value, name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CiWorkflowError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CiWorkflowError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)
