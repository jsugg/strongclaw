"""Shared observability test helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from tests.plugins.infrastructure.context import TestContext


class RecordingExporter(SpanExporter):
    """Collect spans for assertions."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def configure_test_tracing(
    test_context: TestContext,
    observability_module: Any,
) -> RecordingExporter:
    """Install an in-memory OTEL exporter for a test run."""
    exporter = RecordingExporter()
    observability_module.reset_for_tests()
    test_context.env.set("CLAWOPS_OTEL_ENABLED", "1")
    test_context.patch.patch_object(
        observability_module, "_make_span_exporter", new=lambda: exporter
    )
    test_context.patch.patch_object(
        observability_module,
        "_make_span_processor",
        new=lambda span_exporter: SimpleSpanProcessor(span_exporter),
    )
    return exporter
