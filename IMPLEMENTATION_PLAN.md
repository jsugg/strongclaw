# Implementation Plan

## Goal

Build a production-oriented OpenClaw platform rather than a thin install pack. The platform is split into explicit planes:

1. **Control plane**
   - OpenClaw gateway
   - channel ingress
   - memory/session routing
   - human control UI
   - policy attachment points
2. **Execution plane**
   - sandboxed OpenClaw tool sessions
   - ACP/acpx coding workers
   - repository worktrees
   - optional browser-lab runners
3. **Operations plane**
   - Varlock env contract
   - LiteLLM + Postgres
   - OTel Collector
   - backup/restore/retention
   - CI/CD gates
4. **Verification plane**
   - harness suites
   - privacy scans
   - routing/latency charts
   - policy regressions

## Design rules

- Keep the gateway loopback-bound, token-authenticated, and private.
- Keep plugins/skills/MCP off by default unless explicitly reviewed.
- Separate hostile-input reader lanes from privileged mutation lanes.
- Require opposite-family review for high-risk code or infrastructure changes.
- Treat every external side effect as journaled and idempotency-keyed.
- Keep markdown memory as source-of-truth; derived indexes are disposable.
- Prefer OpenClaw built-ins first, then add LiteLLM / OTel / ACP / QMD in layers.

## Phase order

### Phase 0 — secure baseline

Files:
- `platform/configs/openclaw/00-baseline.json5`
- `platform/configs/openclaw/10-trust-zones.json5`
- `scripts/bootstrap/bootstrap_macos.sh`
- `scripts/bootstrap/render_openclaw_config.sh`
- `scripts/bootstrap/verify_baseline.sh`

### Phase 1 — sidecars and budgets

Files:
- `platform/compose/docker-compose.aux-stack.yaml`
- `platform/configs/litellm/config.yaml`
- `platform/configs/otel/collector.yaml`
- `scripts/bootstrap/bootstrap_sidecars.sh`

### Phase 2 — ACP worker plane

Files:
- `platform/configs/openclaw/20-acp-workers.json5`
- `platform/workers/acpx/*`
- `scripts/bootstrap/bootstrap_acpx.sh`
- `scripts/workers/worktree_new.sh`
- `scripts/workers/reviewer_fixer_loop.sh`

### Phase 3 — policy / journaling / wrappers

Files:
- `platform/configs/policy/policy.yaml`
- `platform/configs/policy/*.rego`
- `src/clawops/policy_engine.py`
- `src/clawops/op_journal.py`
- `src/clawops/wrappers/*`

### Phase 4 — context plane

Files:
- `platform/configs/context/context-service.yaml`
- `platform/configs/openclaw/40-qmd-context.json5`
- `platform/configs/openclaw/70-lossless-context-engine.example.json5`
- `src/clawops/context_service.py`

### Phase 5 — channels, observability, browser lab

Files:
- `platform/configs/openclaw/30-channels.json5`
- `platform/configs/openclaw/50-observability.json5`
- `platform/configs/openclaw/60-browser-lab.json5`
- `platform/compose/docker-compose.browser-lab.yaml`
- `platform/compose/docker-compose.langfuse.optional.yaml`
- `scripts/bootstrap/enable_telegram.sh`
- `scripts/bootstrap/enable_whatsapp.sh`
- `scripts/bootstrap/enable_observability.sh`
- `scripts/bootstrap/bootstrap_browser_lab.sh`

### Phase 6 — CI, recovery, Linux migration

Files:
- `.github/workflows/*`
- `security/*`
- `scripts/recovery/*`
- `platform/systemd/*`
- `platform/docs/LINUX_MIGRATION.md`

## What “production-ready” means here

This bundle is production-ready in the sense that it includes:

- real repository layout
- real scripts
- real config overlays
- real companion code
- real tests
- real CI and runbooks
- staged enablement for dangerous capabilities

It intentionally does **not** claim that every third-party optional component is safe to enable on day 0. Browser automation, context-engine plugins, and Langfuse are included as opt-in, staged surfaces.
