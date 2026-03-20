"""Integration tests for structured spans emitted by clawops helpers."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from pytest import MonkeyPatch

from clawops import observability
from clawops.common import write_yaml
from clawops.context_service import service_from_config
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from clawops.wrappers.webhook import invoke_webhook


class RecordingExporter(SpanExporter):
    """Collect spans for assertions."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class _FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, text: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


def _configure_test_tracing(monkeypatch: MonkeyPatch) -> RecordingExporter:
    exporter = RecordingExporter()
    observability.reset_for_tests()
    monkeypatch.setenv("CLAWOPS_OTEL_ENABLED", "1")
    monkeypatch.setattr(observability, "_make_span_exporter", lambda: exporter)
    monkeypatch.setattr(
        observability,
        "_make_span_processor",
        lambda span_exporter: SimpleSpanProcessor(span_exporter),
    )
    return exporter


def test_wrapper_execution_exports_trace_span(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    exporter = _configure_test_tracing(monkeypatch)
    policy_path = tmp_path / "policy.yaml"
    write_yaml(
        policy_path,
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
    journal = OperationJournal(tmp_path / "journal.sqlite")
    journal.init()
    ctx = WrapperContext(policy_engine=PolicyEngine.from_file(policy_path), journal=journal)

    monkeypatch.setattr(
        "clawops.wrappers.base.requests.request",
        lambda *args, **kwargs: _FakeResponse(),
    )

    result = invoke_webhook(
        ctx=ctx,
        url="https://example.internal/hooks/deploy",
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )
    observability.force_flush()

    span = next(span for span in exporter.spans if span.name == "clawops.wrapper.execute")
    assert result["ok"] is True
    assert span.attributes["kind"] == "webhook_post"
    assert span.attributes["status_code"] == 200
    assert span.attributes["request_attempts"] == 1


def test_context_index_exports_trace_span(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    exporter = _configure_test_tracing(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("def validate_jwt():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})

    service = service_from_config(config_path, repo)
    stats = service.index_with_stats()
    observability.force_flush()

    span = next(span for span in exporter.spans if span.name == "clawops.context.index")
    assert stats.indexed_files == 1
    assert span.attributes["repo"] == repo.resolve().as_posix()
    assert span.attributes["indexed_files"] == 1
