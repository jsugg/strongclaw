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
# macOS
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
launchctl kickstart -k gui/$(id -u)/ai.openclaw.sidecars

# Linux
systemctl --user daemon-reload
systemctl --user restart openclaw-sidecars.service
systemctl --user restart openclaw-gateway.service
```

## Health checks

Repository helper scripts that depend on `openclaw` now detect whether the CLI is installed. Tasks that require it fail fast with a clear message, while fallback-capable tasks warn and continue with their fallback path.

```bash
./scripts/bootstrap/doctor_host.sh
make doctor
uv run --project . clawops doctor --skip-runtime
openclaw gateway status --json
openclaw status --all
openclaw doctor
openclaw memory status --deep
openclaw memory search --query "ClawOps" --max-results 1
openclaw security audit --deep
openclaw secrets audit --check
docker compose -f platform/compose/docker-compose.aux-stack.yaml ps
./scripts/ops/check_loopback_bindings.sh
```

## Remote operator access

Tunnel the gateway only:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Keep browser-lab ports local to the hardened session. Do not tunnel `9222` or
`3128` to an operator workstation.

## Render placeholder-backed profiles

```bash
clawops render-openclaw-config \
  --repo-root "$(pwd)" \
  --profile acp
```

If you need to combine a named profile with another placeholder-bearing
overlay, append it explicitly so each fragment is rendered before merge:

```bash
clawops render-openclaw-config \
  --repo-root "$(pwd)" \
  --profile memory-pro-local \
  --overlay platform/configs/openclaw/20-acp-workers.json5
```

Optional: render the companion exec approvals file with repo-local prefixes:

```bash
clawops render-openclaw-config \
  --repo-root "$(pwd)" \
  --profile default \
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

Treat `~/.openclaw/clawops` as service-owned state, not a shared scratch
directory.

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

`op-journal begin` is an audit/bookkeeping primitive. It does not create an
executable wrapper operation by itself.

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

## Memory migration

Treat QMD plus the context service as the repo-document retrieval lane. Use the
vendored `memory-lancedb-pro` plugin only for opt-in durable memory, with
`memory-v2` retained as the migration source until parity is proven.

Export one scope at a time into the import JSON shape that `openclaw memory-pro
import` expects. When you omit `--output` or `--report`, StrongClaw now writes
those artifacts under its OS-specific state directory instead of into the git
checkout.

```bash
clawops memory migrate-v2-to-pro \
  --scope project:strongclaw \
  --output /tmp/project-strongclaw-import.json
```

Apply that snapshot through the same upstream `openclaw memory-pro import`
command shape, but with a ClawOps-managed report artifact:

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

If a live `openclaw memory-pro search` path is already available, switch
`--mode` to `openclaw` or `auto` to compare against the actual plugin-backed
search results.

## Repo workspace contract

Validate the repo layout that ACP workers and rendered overlays expect:

```bash
clawops repo --repo-root "$(pwd)" doctor
```

List, create, and prune managed git worktrees under `repo/worktrees`:

```bash
clawops worktree --repo-root "$(pwd)" list
clawops worktree --repo-root "$(pwd)" new --branch feature/review-lane
clawops worktree --repo-root "$(pwd)" prune
```

## Skill intake

Scan a new skill bundle before enabling it:

```bash
clawops skills scan \
  --source /tmp/downloaded-skill \
  --quarantine-root platform/skills/quarantine \
  --report platform/skills/manifests/downloaded-skill.json
```

Promote only after the bundle has been reviewed against its manifest and hash
trail:

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

The shipped repository workflows declare `base_dir`, so they can be run from
any current working directory. The helper script also pins the repo root
explicitly:

```bash
./scripts/ops/run_workflow.sh platform/configs/workflows/code_review.yaml --dry-run
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

If you are replaying an older approved row created before execution contracts
were introduced, pass the policy file again so the wrapper can restamp the row
before executing:

```bash
clawops wrapper webhook \
  --policy platform/configs/policy/policy.yaml \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --execute-approved
```

## Workflow trust roots

`clawops workflow` now treats workflow YAML as executable code rather than
passive config. By default it only runs workflows from
`platform/configs/workflows/`.

For intentional ad hoc local workflows, use:

```bash
clawops workflow \
  --workflow /tmp/custom-workflow.yaml \
  --allow-untrusted-workflow
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
