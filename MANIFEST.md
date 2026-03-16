# Manifest

This manifest lists the major components shipped in the platform bundle.

## Companion code

- `src/clawops/common.py`
- `src/clawops/json_merge.py`
- `src/clawops/op_journal.py`
- `src/clawops/policy_engine.py`
- `src/clawops/context_service.py`
- `src/clawops/skill_scanner.py`
- `src/clawops/harness.py`
- `src/clawops/charts.py`
- `src/clawops/allowlist_sync.py`
- `src/clawops/workflow_runner.py`
- `src/clawops/wrappers/*`

## OpenClaw overlays

- `platform/configs/openclaw/00-baseline.json5`
- `platform/configs/openclaw/10-trust-zones.json5`
- `platform/configs/openclaw/20-acp-workers.json5`
- `platform/configs/openclaw/30-channels.json5`
- `platform/configs/openclaw/40-qmd-context.json5`
- `platform/configs/openclaw/50-observability.json5`
- `platform/configs/openclaw/60-browser-lab.json5`
- `platform/configs/openclaw/70-lossless-context-engine.example.json5`
- `platform/configs/openclaw/exec-approvals.json`
- `platform/configs/openclaw/channel-model-overrides.example.json5`

## Sidecars

- `platform/compose/docker-compose.aux-stack.yaml`
- `platform/compose/docker-compose.browser-lab.yaml`
- `platform/compose/docker-compose.langfuse.optional.yaml`
- `platform/configs/litellm/config.yaml`
- `platform/configs/otel/collector.yaml`

## Secret / env contract

- `platform/configs/varlock/.env.schema`
- `platform/configs/varlock/.env.local.example`
- `platform/configs/varlock/.env.ci.example`
- `platform/configs/varlock/.env.prod.example`
- `platform/examples/openclaw-secretref-1password.json5`
- `platform/examples/openclaw-secretref-vault.json5`

## Docs

- `platform/docs/*`
- `platform/docs/runbooks/*`

## Scripts

- `scripts/bootstrap/*`
- `scripts/ops/*`
- `scripts/workers/*`
- `scripts/recovery/*`
- `scripts/ci/*`

## Tests

- `tests/*`
