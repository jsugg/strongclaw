# Next Steps

Status: living implementation document

Last updated: 2026-03-16

## Purpose

This document translates the current architecture and audit findings into an
implementation-safe execution plan.

It exists because `IMPLEMENTATION_PLAN.md` is useful as a phase inventory and
architecture note, but it is not a reliable completion tracker. This file is
the working document for implementation status, acceptance criteria, proof, and
rollout sequencing.

Related tracker:

- [`memory-v2.md`](memory-v2.md) covers the opt-in `strongclaw memory v2`
  rollout separately because it has its own OpenClaw compatibility boundary and
  migration plan.

## Current assessment

The repository is materially more than a thin install pack. It has:

- real OpenClaw overlay configs
- real bootstrap and recovery scripts
- real `clawops` companion code
- real CI, security, and runbook assets
- real tests

Current local verification baseline:

- `PYTHONPATH=src pytest -q` -> passed locally (`105 passed` on 2026-03-16)
- `PYTHONPATH=src python3 -m compileall -q src tests` -> passed
- `.venv/bin/pyright src` -> `0 errors, 0 warnings, 0 informations`
- `.venv/bin/ruff check src tests` -> passed

Phase-level assessment:

- Phase 0: substantially implemented
- Phase 1: implemented with deterministic sidecar verification and optional
  runtime probes
- Phase 2: implemented with a `strongclaw` ACP runner layer, branch locking,
  preflight checks, and durable session summaries
- Phase 3: strongly implemented and well tested
- Phase 4: strongly implemented and well tested
- Phase 5: implemented with additive sidecar / observability / channel
  verification surfaces; runtime probes remain optional where local services are
  not running
- Phase 6: implemented with CI, recovery portability, workflow path hardening,
  and Linux migration assets

## Why this needs a separate plan

The strongest parts of the repo are currently the `clawops` internal execution
and verification surfaces:

- policy / journaling / wrappers
- context indexing and packing
- workflow execution
- shell-script regression tests

The thinner parts are the operational orchestration layers around:

- ACP workers
- sidecar bring-up
- observability enablement
- channel enablement

The professional fix is not to redesign the OpenClaw integration. The fix is to
raise implementation depth and verification quality in the `strongclaw` layer
while preserving the current OpenClaw-facing seams.

## OpenClaw compatibility boundary

This plan must not break OpenClaw integration.

Revalidated boundary on 2026-03-16:

- upstream `openclaw/openclaw` does not reference `clawops`, `op_journal`,
  `context_service`, or `~/.openclaw/clawops`
- upstream still documents QMD memory via `memory.backend = "qmd"` and
  `memory.qmd.paths`
- upstream still documents channel access via `dmPolicy` and pairing commands
  such as `openclaw pairing approve telegram <CODE>` and
  `openclaw pairing approve whatsapp <CODE>`

Compatibility constraints:

- do not change the semantics of `platform/configs/openclaw/*.json5` unless the
  work is intentionally about OpenClaw config behavior
- do not change existing `openclaw ...` command shapes used in scripts,
  workflows, and docs
- keep placeholder rendering for local paths in the rendered
  `~/.openclaw/openclaw.json`
- keep `clawops` journal changes additive and backward-loadable for
  `~/.openclaw/clawops/op_journal.sqlite`
- keep wrapper result envelopes backward-compatible; new fields must remain
  optional

## Design principles for the next round

1. Prefer additive verification over semantic rewrites.
2. Strengthen the `strongclaw` layer first; avoid upstream OpenClaw changes.
3. Keep operator-facing command shapes stable.
4. Use staged rollout: add wrappers and proof first, cut over later.
5. Follow the browser-lab pattern:
   config + helper script + verification script + focused regression tests.

## Workstreams

### Workstream 1: split architecture from delivery proof

Problem:

- `IMPLEMENTATION_PLAN.md` mixes architecture intent with implied completion
- it is not detailed enough to serve as an execution tracker

Plan:

- keep `IMPLEMENTATION_PLAN.md` as an architecture / phase inventory document
- use this file as the execution tracker during implementation
- optionally add `IMPLEMENTATION_STATUS.md` later if this file grows too large

Deliverables:

- per-workstream status
- acceptance criteria
- proof commands
- notes on risks, rollbacks, and compatibility

Acceptance criteria:

- phase status is explicit and evidence-backed
- architectural intent and execution status are no longer conflated

### Workstream 2: professionalize the ACP worker plane

Status: completed on 2026-03-16

Problem:

- ACP is present, but the implementation is mostly templates and thin shell
  helpers
- there is no strong local contract around preflight, logging, locking,
  deterministic outputs, or recoverability

Plan:

- add a `strongclaw`-owned ACP runner layer around existing `acpx` commands
- keep current ACP overlays and command backends stable until parity is proven
- add structured worker-session metadata:
  - branch
  - worktree path
  - session type
  - start/finish timestamps
  - exit status
  - output summary path
- add locking around worktree usage so concurrent sessions cannot trample the
  same branch
- add deterministic session output locations under repo-controlled state

Non-goals for the first cut:

- do not change `platform/configs/openclaw/20-acp-workers.json5`
- do not replace `acpx`
- do not require upstream OpenClaw changes

Deliverables:

- ACP runner wrapper(s)
- worker preflight checks
- worker log/summary output contract
- regression or smoke tests around wrapper behavior

Implemented:

- `src/clawops/acp_runner.py`
- `src/clawops/cli.py` (`clawops acp-runner`)
- `scripts/workers/run_codex_session.sh`
- `scripts/workers/run_claude_review.sh`
- `scripts/workers/reviewer_fixer_loop.sh`
- `tests/test_acp_runner.py`
- `tests/test_automation_surfaces.py`

Acceptance criteria:

- ACP sessions fail early on missing prerequisites
- session outputs are durable and machine-readable
- worktree collisions are prevented
- existing operator flows can continue using the current ACP overlay unchanged

Proof:

- `PYTHONPATH=src pytest -q tests/test_acp_runner.py tests/test_automation_surfaces.py`
  -> passed
- `PYTHONPATH=src pytest -q` -> passed locally (`105 passed` on 2026-03-16)

Compatibility notes:

- `acpx` remains the execution backend; the new layer wraps rather than replaces
  it
- `platform/configs/openclaw/20-acp-workers.json5` semantics remain unchanged
- durable session state is additive under repo-controlled `.runs/acp/`

### Workstream 3: expand proof for sidecars and observability

Status: completed on 2026-03-16

Problem:

- sidecar and observability assets are real, but proof is lighter than the
  companion Python tooling

Plan:

- add verification scripts for:
  - sidecar health
  - LiteLLM reachability
  - Postgres reachability
  - OTel collector reachability
  - expected loopback-only bindings
- wire these checks into harness suites and CI where practical
- keep the existing compose files and overlays stable

Deliverables:

- `verify_sidecars.sh`
- `verify_observability.sh`
- harness cases covering the expected health signals

Implemented:

- `src/clawops/platform_verify.py`
- `src/clawops/cli.py` (`clawops verify-platform sidecars|observability`)
- `scripts/bootstrap/verify_sidecars.sh`
- `scripts/bootstrap/verify_observability.sh`
- `scripts/bootstrap/verify_baseline.sh`
- `README.md`
- `QUICKSTART.md`
- `SETUP_GUIDE.md`
- `tests/test_platform_verify.py`
- `tests/test_automation_surfaces.py`
- `tests/test_docs_parity.py`

Acceptance criteria:

- sidecar bring-up has deterministic verification
- observability enablement has explicit post-merge proof
- verification entrypoints are surfaced in the operator docs and baseline gate
- no changes are required to `platform/configs/openclaw/50-observability.json5`
  command semantics

Proof:

- `PYTHONPATH=src python3 -m clawops verify-platform sidecars --repo-root . --skip-runtime`
  -> passed
- `PYTHONPATH=src python3 -m clawops verify-platform observability --repo-root . --skip-runtime`
  -> passed
- `./scripts/bootstrap/verify_sidecars.sh --skip-runtime` -> passed
- `./scripts/bootstrap/verify_observability.sh --skip-runtime` -> passed
- `PYTHONPATH=src pytest -q tests/test_automation_surfaces.py tests/test_docs_parity.py`
  -> passed
- `PYTHONPATH=src pytest -q tests/test_platform_verify.py` -> passed

Compatibility notes:

- `platform/configs/openclaw/50-observability.json5` semantics remain unchanged
- static contract checks are mandatory; runtime probes stay available but
  skippable for hosts where sidecars are not currently running

### Workstream 4: expand proof for channels and allowlists

Status: completed on 2026-03-16

Problem:

- channel overlays and allowlist rendering exist, but the operational proof is
  still relatively thin

Plan:

- add verification around channel overlay merge behavior
- add checks that operator docs, shell scripts, and overlays remain aligned with
  pairing-first workflows
- keep `dmPolicy` and pairing command shapes unchanged

Deliverables:

- channel verification script or harness suite
- parity checks for overlay usage in docs and scripts
- explicit proof for Telegram / WhatsApp enablement flows

Implemented:

- `src/clawops/platform_verify.py`
- `src/clawops/cli.py` (`clawops verify-platform channels`)
- `scripts/bootstrap/verify_channels.sh`
- `README.md`
- `QUICKSTART.md`
- `SETUP_GUIDE.md`
- `tests/test_platform_verify.py`
- `tests/test_automation_surfaces.py`
- `tests/test_docs_parity.py`

Acceptance criteria:

- channel enablement remains aligned with upstream OpenClaw docs
- allowlist rendering and overlay wiring are regression-tested
- channel verification entrypoints are documented beside Telegram / WhatsApp
  enablement flows
- no changes are required to `platform/configs/openclaw/30-channels.json5`
  semantics

Proof:

- `PYTHONPATH=src python3 -m clawops verify-platform channels --repo-root .`
  -> passed
- `./scripts/bootstrap/verify_channels.sh` -> passed
- `PYTHONPATH=src pytest -q tests/test_automation_surfaces.py tests/test_docs_parity.py`
  -> passed
- `PYTHONPATH=src pytest -q tests/test_platform_verify.py` -> passed

Compatibility notes:

- `platform/configs/openclaw/30-channels.json5` semantics remain unchanged
- verification preserves upstream pairing-first workflows and durable allowlist
  rendering instead of altering channel command shapes

### Workstream 5: preserve and extend the strong core

Status: completed on 2026-03-16

Problem:

- the highest-quality parts of the repo are already the `clawops` internals
- new orchestration features should reuse that strength instead of bypassing it

Plan:

- continue building new execution and verification features in `clawops`
- reuse:
  - process execution helpers
  - harness runner
  - workflow runner
  - journaling
  - docs parity tests where helpful

Acceptance criteria:

- new work is implemented as composable, testable strongclaw-layer tooling
- operational scripts are thin wrappers over tested logic where practical

Implemented:

- `src/clawops/process_runner.py`
- `src/clawops/harness.py`
- `src/clawops/workflow_runner.py`
- `src/clawops/cli.py`
- `tests/test_cli.py`
- `tests/test_harness.py`
- `tests/test_workflow_runner.py`
- `tests/test_automation_surfaces.py`
- `tests/test_openclaw_shell_scripts.py`

Proof:

- `PYTHONPATH=src pytest -q tests/test_cli.py tests/test_harness.py tests/test_workflow_runner.py tests/test_automation_surfaces.py`
  -> passed
- `PYTHONPATH=src python3 -m clawops workflow --workflow platform/configs/workflows/daily_healthcheck.yaml --dry-run`
  -> passed

Compatibility notes:

- operational scripts keep delegating to `clawops` entrypoints instead of
  introducing parallel command surfaces
- workflow path hardening stays inside `strongclaw` helpers and does not change
  documented `openclaw ...` command shapes

### Workstream 6: capture phase-6 delivery proof

Status: completed on 2026-03-16

Problem:

- phase 6 assets were already present, but proof was scattered across workflows,
  recovery scripts, shell-script tests, and Linux migration docs
- this tracker did not record enough evidence to distinguish "shipped" from
  "assumed"

Plan:

- surface concrete proof for CI, recovery portability, workflow portability,
  and Linux migration assets without changing OpenClaw-facing behavior

Deliverables:

- explicit phase-6 file inventory
- proof commands for the recovery / workflow / harness surfaces
- compatibility notes for non-repo working-directory execution

Implemented:

- `.github/workflows/harness.yml`
- `.github/workflows/nightly.yml`
- `.github/workflows/security.yml`
- `scripts/bootstrap/run_harness_smoke.sh`
- `scripts/recovery/backup_create.sh`
- `scripts/recovery/backup_verify.sh`
- `scripts/recovery/restore_openclaw.sh`
- `scripts/ops/run_workflow.sh`
- `platform/docs/LINUX_MIGRATION.md`
- `tests/test_openclaw_shell_scripts.py`

Acceptance criteria:

- CI ships at least one harness path and at least one full-suite/compile smoke
- recovery helpers work from outside the repo root and degrade safely when the
  OpenClaw backup CLI is unavailable
- documented workflow entrypoints resolve repo-local paths correctly from
  outside the repo root
- Linux migration assets remain shipped and documented

Proof:

- `PYTHONPATH=src pytest -q tests/test_openclaw_shell_scripts.py` -> passed
- `./scripts/bootstrap/run_harness_smoke.sh ./.runs/audit-proof`
  -> `passed=2 total=2`, `passed=3 total=3`
- `PYTHONPATH=src python3 -m clawops workflow --workflow platform/configs/workflows/daily_healthcheck.yaml --dry-run`
  -> passed
- `PYTHONPATH=src pytest -q` -> passed locally (`105 passed` on 2026-03-16)

Compatibility notes:

- workflow and recovery helpers preserve the documented `openclaw ...` command
  shapes used by scripts and runbooks
- recovery behavior stays additive: when OpenClaw backup tooling is unavailable,
  fallback tar flows remain local-only operator tooling rather than a contract
  change

## Sequencing

Recommended order:

1. Keep this file current as the implementation tracker.
2. Add ACP runner wrappers and worktree/session contracts.
3. Add sidecar / observability verification scripts and harness coverage.
4. Add channel verification and parity coverage.
5. Only after parity is proven, consider cutting existing thin scripts over to
   the new wrappers.

## Rollout strategy

Stage 1:

- add verification and wrapper paths in parallel to existing flows
- do not remove existing scripts

Stage 2:

- document the preferred path once parity is proven
- keep the old path available during transition

Stage 3:

- only retire older paths if they are clearly redundant and coverage is in place

## Proof requirements

Every completed work item in this document should record:

- implementation diff or file list
- test coverage added or updated
- proof commands run
- compatibility notes

Minimum proof expectation for new work:

- targeted tests for the changed behavior
- `PYTHONPATH=src pytest -q`
- `PYTHONPATH=src python3 -m compileall -q src tests`

## Risks

Risk:

- over-correcting thin orchestration by changing OpenClaw-facing config or CLI
  contracts

Mitigation:

- keep all remediation internal to `strongclaw` unless an OpenClaw change is
  explicitly required

Risk:

- building a richer ACP plane that drifts from current operator habits

Mitigation:

- keep current ACP scripts usable during rollout
- add wrappers first, then migrate usage

Risk:

- adding verification that is too brittle for local operators

Mitigation:

- separate hard requirements from optional smoke checks
- prefer preflight checks with actionable failure messages

## Update protocol

When implementation starts, update this file by:

- changing status lines from planned -> in progress -> completed
- adding proof commands and results
- recording any compatibility exceptions explicitly
- linking any new runbooks, tests, or scripts added during the work

## Current status snapshot

- Architecture and repo shape: strong
- Core `clawops` implementation depth: strong
- ACP worker operational maturity: implemented with preflight, locking, and
  durable session summaries
- Sidecar / observability proof depth: strong static proof with optional runtime
  probes
- Channel operational proof depth: strong
- OpenClaw integration safety: currently preserved as long as the compatibility
  constraints above are respected
