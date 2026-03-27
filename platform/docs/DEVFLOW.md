# Devflow

Strongclaw ships a production-oriented development workflow surface at:

```bash
clawops devflow plan
clawops devflow run
clawops devflow status
clawops devflow resume
clawops devflow cancel
clawops devflow audit
```

## Operator Flow

Plan a run:

```bash
clawops devflow plan --repo-root . --goal "Fix regression and add coverage"
```

Execute the run:

```bash
clawops devflow run \
  --repo-root . \
  --goal "Fix regression and add coverage" \
  --approved-by operator
```

Inspect one run:

```bash
clawops devflow status --repo-root . --run-id <run-id>
```

Resume a failed or approval-blocked run:

```bash
clawops devflow resume \
  --repo-root . \
  --run-id <run-id> \
  --approved-by operator
```

Cancel a non-terminal run:

```bash
clawops devflow cancel --repo-root . --run-id <run-id> --requested-by operator
```

Build the audit bundle:

```bash
clawops devflow audit --repo-root . --run-id <run-id>
```

## Run Layout

Each run materializes under:

```text
.clawops/devflow/<run-id>/
  plan.json
  workflow.yaml
  run.json
  artifacts/manifest.json
  summaries/
  audit/
  logs/devflow.log.jsonl
```

## Recovery

- `status --stuck-only` lists stale non-terminal runs from the shared journal.
- `resume` restarts from the first incomplete stage only.
- `audit` bundles run state, stage events, artifact manifest data, and summary payloads.
- verification roles run in isolated workspaces and fail when they mutate tracked files.
