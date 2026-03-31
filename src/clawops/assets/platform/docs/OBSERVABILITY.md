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

## Runtime Bring-Up Events

`clawops ops` now emits structured readiness events:

- `clawops.ops.sidecars.wait.start`
- `clawops.ops.sidecars.wait.ready`
- `clawops.ops.sidecars.wait.timeout`
- `clawops.ops.sidecars.ready`
- `clawops.ops.sidecars.status`

Wait-timeout events include `service`, `target`, `observed`, and `timeout_seconds` so failed dependencies can be diagnosed without replaying the command interactively.

## Host Service Activation Events

`clawops services install --activate` now emits:

- `clawops.services.activate` with `service_manager=launchd` and `step` values:
  - `sidecars_bootstrap`
  - `gateway_bootstrap`
  - `maintenance_bootstrap`
- `clawops.services.activate` with `service_manager=systemd` and `step` values:
  - `daemon_reload`
  - `enable_now` (`unit` included per activation)

These events are intentionally coarse-grained to avoid high-volume per-poll logging.
