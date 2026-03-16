"""Base wrapper logic shared by GitHub, Jira, and webhook helpers."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

import requests

from clawops.op_journal import OperationJournal
from clawops.policy_engine import Decision, PolicyEngine


@dataclasses.dataclass(slots=True)
class WrapperContext:
    """Shared wrapper dependencies."""

    policy_engine: PolicyEngine
    journal: OperationJournal
    dry_run: bool = False

    def evaluate(self, payload: Mapping[str, Any]) -> Decision:
        """Evaluate policy for the requested side effect."""
        return self.policy_engine.evaluate(payload)

    def begin(self, *, scope: str, kind: str, trust_zone: str, target: str, payload: dict[str, Any]) -> str:
        """Create or reuse an operation journal entry and return the op_id."""
        op = self.journal.begin(
            scope=scope,
            kind=kind,
            trust_zone=trust_zone,
            normalized_target=target,
            inputs=payload,
        )
        return op.op_id


class JsonHttpClient:
    """Thin requests wrapper used by all external API helpers."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def post(self, url: str, *, headers: Mapping[str, str], json_body: Mapping[str, Any]) -> requests.Response:
        """Execute a JSON POST."""
        return requests.post(url, headers=dict(headers), json=json_body, timeout=self.timeout)
