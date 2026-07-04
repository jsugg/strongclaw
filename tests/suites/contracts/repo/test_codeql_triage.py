"""Contract coverage for the captured CodeQL baseline triage."""

from __future__ import annotations

import json
from typing import cast

from tests.utils.helpers.repo import REPO_ROOT


def _string_mapping(value: object) -> dict[str, object]:
    """Validate and return a JSON object with string keys."""
    assert isinstance(value, dict)
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        assert isinstance(key, str)
        result[key] = item
    return result


def test_codeql_triage_covers_every_captured_third_party_alert() -> None:
    """The 15-alert baseline must retain one accepted-risk rationale per alert."""
    path = REPO_ROOT / ".github" / "codeql-triage.json"
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    root = _string_mapping(payload)
    alerts = root.get("alerts")
    assert isinstance(alerts, list)

    numbers: list[int] = []
    for raw_alert in cast(list[object], alerts):
        alert = _string_mapping(raw_alert)
        number = alert.get("number")
        assert isinstance(number, int) and not isinstance(number, bool)
        numbers.append(number)
        assert alert.get("disposition") == "accepted_risk_third_party"
        assert isinstance(alert.get("rationale"), str) and str(alert["rationale"]).strip()
        assert isinstance(alert.get("followUp"), str) and str(alert["followUp"]).strip()
        assert str(alert.get("originalLocation", "")).startswith(
            "platform/plugins/memory-lancedb-pro/"
        )
        assert "fixes" not in alert
        assert "tests" not in alert

    assert sorted(numbers) == list(range(1, 16))
    assert root.get("baselineAlertNumbers") == list(range(1, 16))
    assert root.get("baselineDisposition") == "accepted_risk_third_party"
    assert root.get("liveVerificationRequired") is False
    assert root.get("liveDismissedAt") == "2026-07-04T03:18:04Z"
    assert str(root.get("rollbackSnapshot", "")).endswith("codeql-open-alerts.json")
    boundary = _string_mapping(root.get("maintenanceBoundary"))
    assert boundary.get("maintainedPlugin") == "platform/plugins/strongclaw-hypermemory"
    assert boundary.get("thirdPartyPlugin") == "platform/plugins/memory-lancedb-pro"
