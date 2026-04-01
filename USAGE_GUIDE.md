# Usage Guide

This guide is for day-2 operations after the platform is up.

## Start / stop

Manual:

```bash
clawops ops gateway start
clawops ops sidecars up
clawops ops sidecars down
```

Repo-local sidecar state during development:

```bash
clawops ops sidecars up --repo-local-state
clawops ops sidecars down --repo-local-state
```

Service mode:

```bash
# macOS
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
launchctl kickstart -k gui/$(id -u)/ai.openclaw.sidecars

# Linux
systemctl --user daemon-reload
systemctl --user restart openclaw-sidecars.service
systemctl --user restart openclaw-gateway.service
```

## Health checks

Repository helper commands that depend on `openclaw` now detect whether the CLI is installed. Tasks that require it fail fast with a clear message, while fallback-capable tasks warn and continue with their fallback path.

```bash
clawops doctor-host
make doctor
uv run --project . clawops doctor --skip-runtime --no-model-probe
openclaw gateway status --json
openclaw status --all
openclaw doctor
openclaw memory status --deep
openclaw memory search --query "ClawOps" --max-results 1
openclaw security audit --deep
openclaw secrets audit --check
docker compose -f platform/compose/docker-compose.aux-stack.yaml ps
clawops verify-platform sidecars
```

`clawops doctor --skip-runtime --no-model-probe` is explicitly degraded: it is
useful for local host validation, but it is not a production-readiness pass.

For repo-local compose state hygiene during development, prefer targeted tools:

```bash
clawops ops prune-qdrant-test-collections
clawops ops reset-compose-state --component qdrant
clawops ops reset-compose-state --component postgres
```

## Remote operator access

Tunnel the gateway only:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Keep browser-lab ports local to the hardened session. Do not tunnel `9222` or `3128` to an operator workstation.

## Render placeholder-backed profiles

```bash
clawops render-openclaw-config \
  --profile acp
```

If you need to combine a named profile with another placeholder-bearing overlay, append it explicitly so each fragment is rendered before merge:

```bash
clawops render-openclaw-config \
  --profile memory-lancedb-pro \
  --overlay platform/configs/openclaw/20-acp-workers.json5
```

Optional: render the companion exec approvals file with repo-local prefixes:

```bash
clawops render-openclaw-config \
  --profile hypermemory \
  --exec-approvals-output ~/.openclaw/exec-approvals.json
```

## Merge static overlays safely

Static overlays without placeholders can still be merged directly:

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

Treat `~/.openclaw/clawops` as service-owned state, not a shared scratch directory.

- keep directory mode `0700` on `~/.openclaw/clawops`
- keep file mode `0600` on `~/.openclaw/clawops/op_journal.sqlite`
- do not grant write access to lower-trust workers or shared workspaces

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

`op-journal begin` is an audit/bookkeeping primitive. It does not create an executable wrapper operation by itself.

Approve an external action:

```bash
clawops approvals approve \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --approved-by operator \
  --note "approved after review"
```

Inspect the queue or delegate a queued review:

```bash
clawops approvals queue --db ~/.openclaw/clawops/op_journal.sqlite

clawops approvals delegate \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --reviewed-by operator \
  --to reviewer-acp-claude \
  --note "route through ACP reviewer"
```

## Evaluate policy

```bash
clawops policy \
  --policy platform/configs/policy/policy.yaml \
  --input examples/policy-inputs/github-comment.json
```

## Devflow

Use the public devflow surface for staged planning, execution, recovery, and audit:

```bash
clawops devflow plan --goal "Fix regression and add coverage"
clawops devflow run --goal "Fix regression and add coverage" --approved-by operator
clawops devflow status --run-id <run-id>
clawops devflow status --stuck-only
clawops devflow resume --run-id <run-id> --approved-by operator
clawops devflow cancel --run-id <run-id> --requested-by operator
clawops devflow audit --run-id <run-id>
```

Run-local state lands under `.clawops/devflow/<run-id>/` and the audit bundle is written under that run's `audit/` directory.

## Context service

Index:

```bash
clawops context codebase index \
  --scale small \
  --config platform/configs/context/codebase.yaml \
  --repo ~/Projects/myrepo
```

Query:

```bash
clawops context codebase query \
  --scale medium \
  --config platform/configs/context/codebase.yaml \
  --repo ~/Projects/myrepo \
  --query "JWT validation bug"
```

Build a pack:

```bash
clawops context codebase pack \
  --scale medium \
  --config platform/configs/context/codebase.yaml \
  --repo ~/Projects/myrepo \
  --query "regression around auth middleware" \
  --output /tmp/context-pack.md
```

## Hypermemory path

StrongClaw now defaults to the supported sparse+dense memory stack. Bring it up explicitly with:

```bash
export HYPERMEMORY_EMBEDDING_MODEL=openai/text-embedding-3-small
clawops setup --profile hypermemory
clawops hypermemory --config ~/.config/strongclaw/memory/hypermemory.yaml verify
clawops doctor
```

That profile enables the combined `lossless-claw` + `strongclaw-hypermemory` runtime, keeps `autoRecall` on, keeps `autoReflect` off, and verifies the Qdrant dense+sparse backend rather than the legacy built-in QMD path.

If you need the OpenClaw built-ins instead, switch to the explicit fallback:

```bash
clawops config memory --set-profile openclaw-default
```

If you need the built-ins plus the experimental QMD backend, switch to:

```bash
clawops config memory --set-profile openclaw-qmd
```

## Memory migration

Treat QMD plus the context service as the repo-document retrieval lane for the explicit `openclaw-qmd` fallback profile. Use this section only when you need to migrate a standalone or hypermemory `hypermemory` corpus into the vendored `memory-lancedb-pro` plugin.

Export one scope at a time into the import JSON shape that `openclaw memory-pro import` expects. When you omit `--output` or `--report`, StrongClaw now writes those artifacts under its OS-specific state directory instead of into the git checkout.

```bash
clawops memory migrate-hypermemory-to-pro \
  --scope project:strongclaw \
  --output /tmp/project-strongclaw-import.json
```

Apply that snapshot through the same upstream `openclaw memory-pro import` command shape, but with a ClawOps-managed report artifact:

```bash
clawops memory import-pro-snapshot \
  --input /tmp/project-strongclaw-import.json
```

Generate a parity report before you cut over durable writes:

```bash
clawops memory verify-pro-parity \
  --scope project:strongclaw \
  --import-snapshot /tmp/project-strongclaw-import.json \
  --mode import \
  --query "deployment playbook"
```

If a live `openclaw memory-pro search` path is already available, switch `--mode` to `openclaw` or `auto` to compare against the actual plugin-backed search results.

## Repo workspace contract

Validate the repo layout that ACP workers and rendered overlays expect:

```bash
clawops repo doctor
```

List, create, and prune managed git worktrees under `repo/worktrees`:

```bash
clawops worktree list
clawops worktree new --branch feature/review-lane
clawops worktree prune
```

## Skill intake

Scan a new skill bundle before enabling it:

```bash
clawops skills scan \
  --source /tmp/downloaded-skill \
  --quarantine-root platform/skills/quarantine \
  --report platform/skills/manifests/downloaded-skill.json
```

Promote only after the bundle has been reviewed against its manifest and hash trail:

```bash
clawops skills promote \
  --manifest platform/skills/manifests/downloaded-skill.json \
  --skills-root platform/skills \
  --stage reviewed

clawops skills promote \
  --manifest platform/skills/manifests/downloaded-skill.json \
  --skills-root platform/skills \
  --stage approved
```

## Harness

Run the default suites:

```bash
clawops harness \
  --suite platform/configs/harness/security_regressions.yaml \
  --output /tmp/security.jsonl

clawops harness \
  --suite platform/configs/harness/policy_regressions.yaml \
  --output /tmp/policy.jsonl
```

Chart results:

```bash
clawops charts \
  --input /tmp/security.jsonl \
  --output /tmp/security.png
```

## Workflows

Run a repository workflow directly:

```bash
clawops workflow \
  --workflow platform/configs/workflows/code_review.yaml
```

The shipped repository workflows declare `base_dir`, so they can be run from any current working directory. The helper script also pins the repo root explicitly:

```bash
clawops workflow --workflow platform/configs/workflows/code_review.yaml --dry-run
```

## Approval-gated wrappers

Preparing a webhook call may return `pending_approval` instead of executing immediately:

```bash
clawops wrapper webhook \
  --policy platform/configs/policy/policy.yaml \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --scope telegram:owner \
  --trust-zone automation \
  --url https://example.internal/hooks/deploy \
  --payload-file payload.json
```

After approval, execute the saved operation:

```bash
clawops wrapper webhook \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --execute-approved
```

If you are replaying an older approved row created before execution contracts were introduced, pass the policy file again so the wrapper can restamp the row before executing:

```bash
clawops wrapper webhook \
  --policy platform/configs/policy/policy.yaml \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --execute-approved
```

## Workflow trust roots

`clawops workflow` now treats workflow YAML as executable code rather than passive config. By default it only runs workflows from `platform/configs/workflows/`.

For intentional ad hoc local workflows, use:

```bash
clawops workflow \
  --workflow /tmp/custom-workflow.yaml \
  --allow-untrusted-workflow
```

## ACP workers

Use the helper commands:

```bash
clawops acp-runner --prompt "Refactor auth middleware"
clawops workflow --workflow platform/configs/workflows/code_review.yaml --dry-run
clawops worktree new --branch feature/auth-hardening
clawops workflow --workflow platform/configs/workflows/code_review.yaml --dry-run
```

## Recovery

```bash
clawops recovery backup-create
clawops recovery backup-verify latest
clawops recovery restore /path/to/archive.tar.gz
clawops recovery rotate-secrets
```

## Production checklists

Use:

- `platform/docs/PRODUCTION_READINESS_CHECKLIST.md`
- `platform/docs/BACKUP_AND_RECOVERY.md`
- `platform/docs/CI_AND_SECURITY.md`
