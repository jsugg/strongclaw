# Degradation Contract

This document defines how dependency health maps to StrongClaw runtime impact.

## Sidecar Dependency Matrix

| Dependency | Used by | Required for `clawops ops sidecars up` success | Impact when unavailable |
| --- | --- | --- | --- |
| Postgres | runtime metadata/session state | Yes | **fatal**: sidecar bring-up fails |
| LiteLLM | loopback model routing | Yes | **fatal**: sidecar bring-up fails |
| Qdrant | dense/sparse retrieval (`openclaw-qmd`, `hypermemory`) | Required when the active profile uses QMD or hypermemory retrieval | **degraded**: retrieval lanes depending on Qdrant are unavailable |
| Neo4j | graph expansion for `hypermemory` context | Required when the active profile uses hypermemory graph expansion | **degraded**: graph expansion is unavailable |
| OTel Collector | telemetry export | No | **observational**: tracing/metrics export unavailable, runtime behavior unaffected |

## Operator Output Contract

- `clawops ops status` and `clawops ops sidecars up --json` report dependency health under `readiness.required` and `readiness.optional`.
- Each readiness entry includes:
  - `service`
  - `required`
  - `impact` (`fatal`, `degraded`, or `observational`)
  - `reason`
  - `ready`
  - expected vs observed state/health fields
- `ok` and `readiness.requiredReady` are `true` only when every required dependency is ready.

## Plugin Startup Contract

- `strongclaw-hypermemory` runs startup preflight (`clawops hypermemory status --json`) before serving memory tools.
- If preflight fails, tool responses return a disabled/unavailable payload instead of silent fallback.
- Existing configs that only define `timeoutMs` remain valid; startup/tool timeout split is optional via `startupTimeoutMs` and `toolTimeoutMs`.
- Per-operation timeout classes are optional: read operations can use `shortTimeoutMs`, and write/index/maintenance operations can use `longTimeoutMs`.

## Related Docs

- [Plugin Inventory](./PLUGIN_INVENTORY.md)
- [Observability](./OBSERVABILITY.md)
- [Hypermemory](./HYPERMEMORY.md)
