# Observability

## Included now

- OTel Collector config
- OpenClaw diagnostics overlay
- LiteLLM callbacks
- harness charts

## Included later / optional

- optional Langfuse compose file
- OTLP exporter config for Langfuse

## Redaction rule

Collector-side redaction is mandatory before broader trace export. Do not assume console redaction protects OTLP or file-log payloads.
