"""Integration tests for structured spans emitted by clawops helpers."""

from __future__ import annotations

import pathlib

from clawops import observability
from clawops.context_service import service_from_config
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from clawops.wrappers.webhook import invoke_webhook
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.context import build_context_repo
from tests.utils.helpers.journal import create_journal
from tests.utils.helpers.observability import RecordingExporter
from tests.utils.helpers.policy import write_policy_file


class _FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, text: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


def test_wrapper_execution_exports_trace_span(
    tmp_path: pathlib.Path,
    test_context: TestContext,
    tracing_exporter: RecordingExporter,
) -> None:
    policy_path = write_policy_file(
        tmp_path / "policy.yaml",
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "automation": {
                    "allow_actions": ["webhook.post"],
                    "allow_categories": ["external_write"],
                }
            },
            "allowlists": {"webhook_url": ["https://example.internal/hooks/deploy"]},
        },
    )
    journal = create_journal(tmp_path / "journal.sqlite")
    ctx = WrapperContext(policy_engine=PolicyEngine.from_file(policy_path), journal=journal)

    def _fake_request(*args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        return _FakeResponse()

    test_context.patch.patch("clawops.wrappers.base.requests.request", new=_fake_request)

    result = invoke_webhook(
        ctx=ctx,
        url="https://example.internal/hooks/deploy",
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )
    observability.force_flush()

    span = next(span for span in tracing_exporter.spans if span.name == "clawops.wrapper.execute")
    attributes = span.attributes
    assert attributes is not None
    assert result["ok"] is True
    assert attributes["kind"] == "webhook_post"
    assert attributes["status_code"] == 200
    assert attributes["request_attempts"] == 1


def test_context_index_exports_trace_span(
    tmp_path: pathlib.Path,
    tracing_exporter: RecordingExporter,
) -> None:
    repo, config_path = build_context_repo(
        tmp_path,
        files={"auth.py": "def validate_jwt():\n    return True\n"},
    )

    service = service_from_config(config_path, repo)
    stats = service.index_with_stats()
    observability.force_flush()

    span = next(span for span in tracing_exporter.spans if span.name == "clawops.context.index")
    attributes = span.attributes
    assert attributes is not None
    assert stats.indexed_files == 1
    assert attributes["repo"] == repo.resolve().as_posix()
    assert attributes["indexed_files"] == 1
