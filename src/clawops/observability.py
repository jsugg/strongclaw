"""Structured logging and optional OpenTelemetry span export helpers."""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import os
import sys
import threading
from collections.abc import Iterator, Mapping
from typing import Any

from clawops import __version__
from clawops.common import canonical_json

type TelemetryScalar = bool | int | float | str
type TelemetryValue = TelemetryScalar | None

_TRACER_LOCK = threading.Lock()
_tracer_state: _TracerState | None = None


def _parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean-like value")


def structured_logs_enabled() -> bool:
    """Return whether structured stderr logs are enabled."""
    return _parse_bool_env("CLAWOPS_STRUCTURED_LOGS", default=False)


def tracing_enabled() -> bool:
    """Return whether OTLP trace export is enabled."""
    if "CLAWOPS_OTEL_ENABLED" in os.environ:
        return _parse_bool_env("CLAWOPS_OTEL_ENABLED", default=False)
    return any(
        os.environ.get(name)
        for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _coerce_telemetry_value(value: TelemetryValue) -> TelemetryScalar | None:
    """Convert a telemetry value into a supported scalar."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    return str(value)


def emit_structured_log(event: str, payload: Mapping[str, TelemetryValue]) -> None:
    """Write one structured observability line to stderr when enabled."""
    if not structured_logs_enabled():
        return
    record: dict[str, TelemetryScalar] = {"event": event}
    for key, value in payload.items():
        coerced = _coerce_telemetry_value(value)
        if coerced is not None:
            record[key] = coerced
    sys.stderr.write(canonical_json(record) + "\n")


def _make_span_exporter() -> Any:
    """Create the OTLP span exporter."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter()


def _make_span_processor(exporter: Any) -> Any:
    """Create the span processor used by the tracer provider."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(exporter)


@dataclasses.dataclass(slots=True)
class _TracerState:
    """Cached tracer provider state."""

    provider: Any
    tracer: Any


def _build_tracer_state() -> _TracerState | None:
    """Create the tracer provider if tracing is enabled."""
    if not tracing_enabled():
        return None
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create(
        {
            "service.name": os.environ.get("CLAWOPS_OTEL_SERVICE_NAME", "clawops"),
            "service.version": __version__,
        }
    )
    provider = TracerProvider(resource=resource)
    processor = _make_span_processor(_make_span_exporter())
    provider.add_span_processor(processor)
    atexit.register(provider.shutdown)
    return _TracerState(provider=provider, tracer=provider.get_tracer("clawops", __version__))


def _get_tracer_state() -> _TracerState | None:
    """Return the cached tracer state."""
    global _tracer_state
    with _TRACER_LOCK:
        if _tracer_state is None:
            _tracer_state = _build_tracer_state()
        return _tracer_state


class ObservedSpan:
    """Thin no-op-safe wrapper around an OpenTelemetry span."""

    __slots__ = ("_span",)

    def __init__(self, span: Any | None) -> None:
        self._span = span

    def set_attributes(self, payload: Mapping[str, TelemetryValue]) -> None:
        """Attach scalar attributes to the span when tracing is enabled."""
        if self._span is None:
            return
        for key, value in payload.items():
            coerced = _coerce_telemetry_value(value)
            if coerced is not None:
                self._span.set_attribute(key, coerced)

    def record_exception(self, exc: BaseException) -> None:
        """Record an exception on the span when possible."""
        if self._span is None:
            return
        self._span.record_exception(exc)

    def set_error(self, description: str | None = None) -> None:
        """Mark the span as failed."""
        if self._span is None:
            return
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, description))


@contextlib.contextmanager
def observed_span(
    name: str, *, attributes: Mapping[str, TelemetryValue] | None = None
) -> Iterator[ObservedSpan]:
    """Yield a tracing span when OTLP export is enabled."""
    tracer_state = _get_tracer_state()
    if tracer_state is None:
        span = ObservedSpan(None)
        if attributes is not None:
            span.set_attributes(attributes)
        yield span
        return
    with tracer_state.tracer.start_as_current_span(name) as raw_span:
        span = ObservedSpan(raw_span)
        if attributes is not None:
            span.set_attributes(attributes)
        yield span


def force_flush() -> None:
    """Flush any queued telemetry spans."""
    tracer_state = _get_tracer_state()
    if tracer_state is not None:
        tracer_state.provider.force_flush()


def reset_for_tests() -> None:
    """Reset cached telemetry state for deterministic tests."""
    global _tracer_state
    with _TRACER_LOCK:
        if _tracer_state is not None:
            _tracer_state.provider.shutdown()
        _tracer_state = None
