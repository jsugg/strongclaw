# Usage Guide

This guide is for day-2 operations after the platform is up.

## Start / stop

Manual:

```bash
./scripts/ops/launch_gateway_with_varlock.sh
./scripts/ops/launch_sidecars_with_varlock.sh
./scripts/ops/stop_sidecars.sh
```

Service mode:

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
launchctl kickstart -k gui/$(id -u)/ai.openclaw.sidecars
```

## Health checks

```bash
openclaw gateway status --json
openclaw status --all
openclaw doctor
openclaw security audit --deep
openclaw secrets audit --check
docker compose -f platform/compose/docker-compose.aux-stack.yaml ps
```

## Merge overlays safely

```bash
clawops merge-json \
  --base ~/.openclaw/openclaw.json \
  --overlay platform/configs/openclaw/50-observability.json5 \
  --output ~/.openclaw/openclaw.json
```

## Use the operation journal

Initialize:

```bash
clawops op-journal init --db ~/.openclaw/clawops/op_journal.sqlite
```

Begin an external action:

```bash
clawops op-journal begin \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --scope telegram:owner \
  --kind webhook_call \
  --trust-zone automation \
  --target https://example.internal/hooks/deploy \
  --payload-file payload.json
```

## Evaluate policy

```bash
clawops policy evaluate \
  --policy platform/configs/policy/policy.yaml \
  --input examples/policy-inputs/github-comment.json
```

## Context service

Index:

```bash
clawops context index \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/myrepo
```

Query:

```bash
clawops context query \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/myrepo \
  --query "JWT validation bug"
```

Build a pack:

```bash
clawops context pack \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/myrepo \
  --query "regression around auth middleware" \
  --output /tmp/context-pack.md
```

## Skill intake

Scan a new skill bundle before enabling it:

```bash
clawops skill-scan \
  --source /tmp/downloaded-skill \
  --quarantine platform/skills/quarantine \
  --report platform/skills/quarantine/reports/scan.json
```

## Harness

Run the default suites:

```bash
clawops harness run \
  --suite platform/configs/harness/security_regressions.yaml \
  --output ./.runs/security.jsonl

clawops harness run \
  --suite platform/configs/harness/policy_regressions.yaml \
  --output ./.runs/policy.jsonl
```

Chart results:

```bash
clawops charts \
  --input ./.runs/security.jsonl \
  --output ./.runs/security.png
```

## ACP workers

Use the helper scripts:

```bash
./scripts/workers/run_codex_session.sh "Refactor auth middleware"
./scripts/workers/run_claude_review.sh "Review the proposed patch"
./scripts/workers/worktree_new.sh feature/auth-hardening
./scripts/workers/reviewer_fixer_loop.sh feature/auth-hardening
```

## Recovery

```bash
./scripts/recovery/backup_create.sh
./scripts/recovery/backup_verify.sh latest
./scripts/recovery/restore_openclaw.sh /path/to/archive.tar.gz
./scripts/recovery/rotate_secrets.sh
```

## Production checklists

Use:

- `platform/docs/PRODUCTION_READINESS_CHECKLIST.md`
- `platform/docs/BACKUP_AND_RECOVERY.md`
- `platform/docs/CI_AND_SECURITY.md`
