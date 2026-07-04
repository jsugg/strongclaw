"""Unit coverage for report-only CodeQL alert-age evidence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.utils.helpers._ci_workflows.codeql_alert_age import (
    build_codeql_alert_age_report,
    render_codeql_alert_age_markdown,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError

_NOW = datetime(2026, 7, 4, tzinfo=timezone.utc)


def _alert(
    number: int,
    *,
    severity: str,
    created_at: str,
    state: str = "open",
    tool_name: str = "CodeQL",
) -> dict[str, object]:
    """Build one representative GitHub code-scanning API alert."""
    return {
        "number": number,
        "state": state,
        "tool": {"name": tool_name},
        "created_at": created_at,
        "html_url": f"https://github.com/jsugg/strongclaw/security/code-scanning/{number}",
        "rule": {
            "id": f"js/test-{number}",
            "security_severity_level": severity,
        },
    }


def test_codeql_alert_age_filters_sorts_and_marks_stale_alerts() -> None:
    """Only open high/critical alerts should appear in the report-only inventory."""
    payload: list[object] = [
        [
            _alert(1, severity="high", created_at="2026-05-01T00:00:00Z"),
            _alert(2, severity="medium", created_at="2025-01-01T00:00:00Z"),
            _alert(
                5,
                severity="critical",
                created_at="2025-01-01T00:00:00Z",
                tool_name="Semgrep",
            ),
        ],
        [
            _alert(3, severity="critical", created_at="2026-06-25T00:00:00Z"),
            _alert(
                4,
                severity="critical",
                created_at="2025-01-01T00:00:00Z",
                state="dismissed",
            ),
        ],
    ]

    report = build_codeql_alert_age_report(
        payload,
        repository="jsugg/strongclaw",
        stale_days=30,
        generated_at=_NOW,
    )

    assert [alert.number for alert in report.alerts] == [1, 3]
    assert report.stale_count == 1
    assert report.alerts[0].stale is True
    assert report.alerts[1].stale is False
    assert report.to_json()["summary"] == {
        "open_high_critical": 2,
        "stale_high_critical": 1,
    }


def test_codeql_alert_age_markdown_declares_report_only_semantics() -> None:
    """Summary output should make non-enforcement explicit."""
    report = build_codeql_alert_age_report(
        [_alert(7, severity="high", created_at="2026-01-01T00:00:00Z")],
        repository="jsugg/strongclaw",
        generated_at=_NOW,
    )

    markdown = render_codeql_alert_age_markdown(report)

    assert "Report-only" in markdown
    assert "[#7]" in markdown
    assert "184 days" in markdown


@pytest.mark.parametrize(
    ("repository", "stale_days"),
    [("not-a-repository", 30), ("jsugg/strongclaw", 0)],
)
def test_codeql_alert_age_rejects_invalid_policy_inputs(
    repository: str,
    stale_days: int,
) -> None:
    """Invalid trust-boundary inputs should fail with actionable diagnostics."""
    with pytest.raises(CiWorkflowError):
        build_codeql_alert_age_report(
            [],
            repository=repository,
            stale_days=stale_days,
            generated_at=_NOW,
        )


def test_codeql_alert_age_rejects_malformed_tracked_alerts() -> None:
    """Tracked alerts must carry valid timestamps and identifiers."""
    malformed = _alert(9, severity="high", created_at="not-a-timestamp")

    with pytest.raises(CiWorkflowError, match="ISO-8601"):
        build_codeql_alert_age_report(
            [malformed],
            repository="jsugg/strongclaw",
            generated_at=_NOW,
        )
