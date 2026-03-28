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

Use `--project-root <path>` when the control root for run state, journals, and
audit artifacts is not the current working directory.

## Operator Flow

Plan a run:

```bash
clawops devflow plan --goal "Fix regression and add coverage"
```

Execute the run:

```bash
clawops devflow run \
  --goal "Fix regression and add coverage" \
  --approved-by operator
```

Inspect one run:

```bash
clawops devflow status --run-id <run-id>
```

Resume a failed or approval-blocked run:

```bash
clawops devflow resume \
  --run-id <run-id> \
  --approved-by operator
```

Cancel a non-terminal run:

```bash
clawops devflow cancel --run-id <run-id> --requested-by operator
```

Build the audit bundle:

```bash
clawops devflow audit --run-id <run-id>
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

## Artifact Enforcement

- each stage validates its role-declared required artifacts before the stage can promote
- the compiled stage workflow runs `artifact_gate` immediately after worker dispatch
- `artifacts/manifest.json` still records the stage result for audit, including `validated` and `missing_artifacts`

## Recovery

- `status --stuck-only` lists stale non-terminal runs from the shared journal.
- `resume` restarts from the first incomplete stage only.
- `audit` bundles run state, stage events, artifact manifest data, and summary payloads.
- verification roles run in isolated workspaces and fail when they mutate tracked files.
- required stage artifacts are enforced before a stage is promoted; missing required outputs fail the stage and are also recorded in `artifacts/manifest.json`.
