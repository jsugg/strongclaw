# Observability

## Included now

- OTel Collector config
- OpenClaw diagnostics overlay
- LiteLLM callbacks
- harness charts
- ClawOps structured stderr logs via `CLAWOPS_STRUCTURED_LOGS=1`
- ClawOps OTLP spans via `CLAWOPS_OTEL_ENABLED=1` or `OTEL_EXPORTER_OTLP_*`

## Included later / optional

- optional Langfuse compose file
- OTLP exporter config for Langfuse

## Redaction rule

Collector-side redaction is mandatory before broader trace export. Do not assume console redaction protects OTLP or file-log payloads.

## Hypermemory Signals

`strongclaw-hypermemory` now emits structured logs and OTLP spans for:

- reindex runs
- embedding batches and embedding failures
- Qdrant query latency
- lexical planning latency
- fusion latency
- rerank latency
- explicit fallback activation when dense search degrades to SQLite
- vector sync runs and sync failures

Those signals reuse the shared ClawOps telemetry path instead of adding a separate exporter or collector.
