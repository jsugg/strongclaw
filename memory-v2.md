# Strongclaw Memory V2 Plan

This document is the living implementation tracker for designing and shipping a `strongclaw memory v2` that is better than the currently available OpenClaw memory stacks while remaining compatible with OpenClaw.

It is based on the current workspace state and upstream OpenClaw source inspection as of 2026-03-16. Update it after each meaningful design or implementation milestone.

## Purpose

- record the current validated state
- capture the concrete architectural gaps in the current memory setup
- define the compatibility constraints that must not be broken
- document the target `strongclaw memory v2` architecture
- define the phased implementation and migration plan
- record validation results, decisions, deviations, and remaining risks

## Current Context

The current repository now enables default-on semantic memory retrieval, but not a full memory v2:

- `clawops` still provides a separate lexical repo context service in `src/clawops/context_service.py`
- the default rendered OpenClaw config now enables `memory.backend = "qmd"`
- the default QMD corpus is rendered from the local repo root and includes:
  - `platform/docs`
  - `platform/skills`
  - top-level operator guides
  - `memory.md`
  - `platform/workspace/shared/MEMORY.md`
- trust-zone and ACP worker surfaces still expose read-side memory tools only:
  - `memory_search`
  - `memory_get`
- `strongclaw` policy currently models read-side memory actions only:
  - `memory.search`
  - `memory.get`
- there is still no structured durable memory write path in this repository
- there is still no CI-backed runtime proof of live gateway retrieval quality

## Current Evidence

Validated from this codebase:

- lexical repo context service:
  - `src/clawops/context_service.py`
  - `platform/docs/CONTEXT_SERVICE.md`
- default OpenClaw render path:
  - `scripts/bootstrap/render_openclaw_config.sh`
- baseline OpenClaw config defaults:
  - `platform/configs/openclaw/00-baseline.json5`
  - `platform/configs/openclaw/40-qmd-context.json5`
- trust-zone and ACP memory tool exposure:
  - `platform/configs/openclaw/10-trust-zones.json5`
  - `platform/configs/openclaw/20-acp-workers.json5`
- current read-side policy model:
  - `platform/configs/policy/policy.yaml`

Validated from upstream OpenClaw source:

- inspected clone:
  - `/tmp/openclaw-latest`
- inspected commit:
  - `94a01c978907f9ef4d1b23efc7767ee18c77769b`
- stable bundled memory plugin surface:
  - `extensions/memory-core/index.ts`
- stable memory manager contract:
  - `src/memory/types.ts`
- builtin memory manager and derived index:
  - `src/memory/manager.ts`
  - `src/memory/manager-sync-ops.ts`
  - `src/memory/memory-schema.ts`
- QMD memory backend and fallback behavior:
  - `src/memory/qmd-manager.ts`
  - `src/memory/search-manager.ts`
- official durable-write-adjacent behavior:
  - `src/auto-reply/reply/memory-flush.ts`
  - `src/hooks/bundled/session-memory/handler.ts`
- upstream research architecture:
  - `docs/experiments/research/memory.md`

Validated from strongest local community memory plugin inspected:

- inspected clone:
  - `/tmp/memory-lancedb-pro`
- inspected commit:
  - `58ee1ea`
- key files:
  - `index.ts`
  - `src/scopes.ts`
  - `src/reflection-store.ts`

## Key Findings

### 1. OpenClaw's stable contract is narrower than its internal implementations

The stable agent-facing memory contract is still:

- `memory_search`
- `memory_get`

Those tools are served by the bundled memory plugin and backend manager interface, regardless of whether the backend is builtin or QMD.

Implication:

- `strongclaw memory v2` should preserve `memory_search` and `memory_get`
- changing the primary tool names would create unnecessary incompatibility with OpenClaw and this repo's trust/policy setup

### 2. OpenClaw builtin memory is stronger than it looks, but still retrieval-first

The builtin manager already provides:

- a derived SQLite store
- FTS5
- optional `sqlite-vec`
- hybrid keyword + vector retrieval
- optional MMR
- optional temporal decay
- extra path indexing
- session transcript indexing

But it is still primarily a retrieval system over Markdown and session files, not a typed durable memory system with explicit facts, entities, opinions, evidence, and governed writes.

### 3. QMD improves retrieval, not durable memory semantics

QMD is currently the best official fit for repo-wide Markdown retrieval in this repository, but it is still a retrieval backend:

- it preserves `memory_search` / `memory_get`
- it indexes configured collections
- it can include session exports
- it falls back to builtin memory when QMD fails

It does not solve:

- typed memory
- confidence and contradiction tracking
- evidence graphing
- governed durable writes
- robust reflect pipelines

### 4. Official durable memory writes are still mostly "write Markdown files"

OpenClaw's official write path remains largely unstructured:

- compaction flush writes to `memory/YYYY-MM-DD.md`
- bundled session-memory hook writes summary markdown files
- `MEMORY.md` remains curated and mostly human/agent edited

This matches OpenClaw's research direction that Markdown stays canonical, but it means the official shipped stack has not yet implemented the research architecture in full.

### 5. Community plugins are ahead on features, but diverge from the research direction

The strongest community implementation inspected, `memory-lancedb-pro`, adds:

- richer retrieval
- smart extraction
- scope management
- reflection storage
- lifecycle decay and tiering

But it is fundamentally a LanceDB-first architecture with optional Markdown mirroring, not a Markdown-canonical system with a derived disposable index.

Implication:

- `strongclaw` can plausibly build something better if it combines:
  - OpenClaw-compatible tool and hook integration
  - Markdown source-of-truth
  - richer typed derived memory
  - governed writes and reflection

## Problem Statement

The repository now has default-on semantic retrieval, but not a `strongclaw memory v2`.

The missing capabilities are:

- typed durable memory beyond snippet retrieval
- explicit memory writes governed by policy and trust zones
- evidence, confidence, and contradiction tracking
- a real retain / recall / reflect loop
- separation of corpus retrieval from durable memory retrieval
- compatibility-safe integration that does not break OpenClaw

## Compatibility Invariants

The implementation must not break these boundaries:

### OpenClaw tool compatibility

- preserve `memory_search`
- preserve `memory_get`
- preserve their expected result shapes and behavior

### OpenClaw config compatibility

- do not break `plugins.slots.memory`
- do not break `memory.backend`
- do not break current trust-zone tool names
- do not break current workflow command shapes

### OpenClaw architecture compatibility

- keep Markdown as source-of-truth
- keep the derived store rebuildable
- use OpenClaw hooks and plugin surfaces rather than bypassing them

### Strongclaw compatibility

- do not replace or remove `clawops` lexical repo context service
- do not break current QMD-backed retrieval rollout
- do not silently change trust/policy expectations for read-only agents

## Target End State

The desired professional outcome is:

- `strongclaw` ships a memory system that is OpenClaw-compatible and local-first
- canonical memory remains stored in Markdown and typed bank pages
- a richer derived store supports:
  - corpus retrieval
  - durable fact retrieval
  - entity-centric recall
  - opinion/confidence recall
  - evidence-backed citations
  - scoped access
- the stable agent-facing tools still include:
  - `memory_search`
  - `memory_get`
- additive gated tools exist for durable memory operations:
  - `memory_store`
  - `memory_update`
  - `memory_reflect`
- recall quality is benchmarked against:
  - builtin OpenClaw memory
  - QMD
  - `memory-lancedb-pro`
- the implementation is governed by policy and proven not to break OpenClaw integration

## Recommended Architecture

### Layer 1: `strongclaw-memory-core`

A separable core library responsible for:

- Markdown parsing and normalization
- typed bank-file parsing
- derived SQLite schema
- indexing and rebuilds
- retrieval and ranking
- evidence/confidence modeling
- reflection and lifecycle logic
- migration helpers

This layer should have no hard dependency on the OpenClaw runtime.

### Layer 2: `strongclaw-memory-openclaw`

An OpenClaw memory plugin responsible for:

- registering `memory_search`
- registering `memory_get`
- optionally registering additive gated memory tools
- wiring OpenClaw hook events into retain / reflect flows
- exposing status and diagnostics

This layer should be thin and compatibility-focused.

### Keep `clawops` separate

`clawops` should remain:

- repo context indexing
- context-pack generation
- policy evaluation
- workflow orchestration
- journaling and wrappers

It should not become the primary memory runtime.

## Proposed Memory Model

Canonical Markdown layout should evolve toward the upstream research direction:

```text
workspace/
  MEMORY.md
  memory/
    YYYY-MM-DD.md
  bank/
    world.md
    experience.md
    opinions.md
    entities/
      <Entity>.md
```

Derived store should include explicit structures for:

- documents
- chunks
- facts
- entities
- opinions
- evidence links
- reflections
- scopes
- confidence and contradiction signals
- timestamps and validity windows

Two retrieval lanes should be modeled explicitly:

- corpus lane
  - docs
  - runbooks
  - skills
  - guides
- durable memory lane
  - retained facts
  - typed bank pages
  - reflections
  - entity/opinion structures

## Integration Seams To Use

OpenClaw hooks and runtime seams that should drive memory v2:

- `before_agent_start`
- `before_prompt_build`
- `agent_end`
- `before_compaction`
- `session_end`
- `before_reset`
- `before_message_write`
- `tool_result_persist`

These are the safest professional extension points because they already exist in upstream OpenClaw and avoid patching core runtime behavior.

## Best Implementation Approach

### Phase 0. Benchmark and freeze interfaces

Scope:

- define the compatibility contract for `memory_search` and `memory_get`
- define the target additive tools
- define the benchmark query set
- define the migration and storage invariants

Exit criteria:

- written interface contract
- benchmark suite fixture set
- migration invariants documented

### Phase 1. Build the core library in shadow mode

Scope:

- implement canonical Markdown parsers
- implement typed bank-file parsers
- implement the derived SQLite schema
- implement indexing from current Markdown sources and session artifacts
- run shadow indexing only, with no behavior change

Recommended direction:

- keep the first pass deterministic and auditable
- make rebuilds idempotent
- keep the schema explicit and documented

Exit criteria:

- shadow index builds successfully from current repo and workspace sources
- rebuilds are repeatable
- no runtime behavior changes yet

### Phase 2. Implement retrieval parity

Scope:

- implement `memory_search` compatibility over the v2 derived store
- implement `memory_get` compatibility over canonical Markdown
- preserve current citations and source semantics

Recommended direction:

- start by matching current behavior before adding richer ranking
- keep fallback and degraded-mode reporting explicit

Exit criteria:

- v2 serves parity-compatible `memory_search` and `memory_get`
- compatibility tests against OpenClaw expectations pass

### Phase 3. Add richer retrieval without breaking compatibility

Scope:

- add typed fact/entity/opinion retrieval
- add hybrid ranking over corpus and durable memory lanes
- add evidence- and confidence-aware scoring
- add explicit source separation in ranking

Recommended direction:

- keep the stable tool outputs compatible
- put richer detail in status/metadata rather than breaking callers

Exit criteria:

- recall quality improves on the benchmark set
- citations remain correct
- existing tool consumers do not break

### Phase 4. Add governed durable write tools

Scope:

- add additive tools:
  - `memory_store`
  - `memory_update`
  - `memory_reflect`
- write back to canonical Markdown or typed bank pages
- rebuild or patch the derived store accordingly

Recommended direction:

- writes must be explicit, scoped, and auditable
- do not enable write tools for all agents by default
- encode write permissions in `strongclaw` policy and trust-zone config

Exit criteria:

- durable writes work end to end
- unauthorized agents cannot perform them
- write provenance is auditable

### Phase 5. Implement retain / reflect

Scope:

- capture candidate facts from session lifecycle events
- generate retained facts and typed summaries
- update opinions with confidence and contradiction handling
- emit curated bank-page updates

Recommended direction:

- keep reflection outputs reviewable and Markdown-backed
- separate observed facts from inferred summaries
- attach evidence wherever possible

Exit criteria:

- reflection writes stable, reviewable outputs
- evidence links remain valid
- opinion updates are explainable

### Phase 6. Introduce opt-in OpenClaw profile

Scope:

- package the plugin as an opt-in memory slot
- keep existing QMD setup available
- add overlay/config path for v2 trials

Recommended direction:

- do not replace the current default in the first rollout
- keep rollback trivial

Exit criteria:

- opt-in v2 profile works without breaking baseline OpenClaw behavior
- rollback to QMD is trivial

### Phase 7. Promote only after proof

Scope:

- compare v2 against builtin, QMD, and `memory-lancedb-pro`
- prove compatibility and migration safety
- decide whether to promote v2 to default

Recommended direction:

- require benchmark proof, not intuition
- require policy and trust review before default promotion

Exit criteria:

- v2 is clearly better on `strongclaw`'s workload
- OpenClaw compatibility remains intact
- operational risk is acceptable

## Validation Matrix

Minimum required validation:

- compatibility tests for:
  - `memory_search`
  - `memory_get`
  - current config overlays
  - current trust-zone tool assumptions
- rebuild tests proving derived store is reconstructible from Markdown
- migration tests from current workspace memory and QMD-indexed corpus
- golden-query benchmark against:
  - builtin memory
  - QMD
  - `memory-lancedb-pro`
- negative tests proving unauthorized agents cannot write memory
- citation and evidence integrity tests
- degraded-mode tests proving failures are surfaced clearly

## Open Questions

These must be resolved during implementation:

- should v2 ultimately replace QMD retrieval for the default profile, or remain a separate memory slot?
- how much of the bank-file structure should be authored by reflection versus direct human editing?
- should the first writeable scope be agent-private only, or include project/shared scopes from day one?
- should reflection update `MEMORY.md` directly, or only bank pages plus reviewable proposals?
- what benchmark query set best represents `strongclaw`'s real workload?

## Non-Goals For The First Pass

- replacing the `clawops` lexical context service
- forking OpenClaw core runtime
- changing current OpenClaw tool names for read-side memory
- enabling writeable memory for every agent by default
- building a cloud-dependent memory stack

## Risks

### 1. OpenClaw contract drift

Risk:

- a custom plugin that changes tool names or result shapes would break integration assumptions

Mitigation:

- preserve `memory_search` and `memory_get`
- keep additive tools gated and optional

### 2. DB-first drift away from Markdown source-of-truth

Risk:

- a convenience-first vector store implementation could diverge from the canonical memory model and become hard to audit

Mitigation:

- keep Markdown and bank pages canonical
- require rebuildability

### 3. Silent quality degradation

Risk:

- fallback behavior can hide broken retrieval or ranking paths

Mitigation:

- expose degraded mode explicitly in status and diagnostics
- benchmark continuously

### 4. Governance failure on writes

Risk:

- writeable memory without policy and trust controls creates a high-risk mutation surface

Mitigation:

- keep write tools opt-in
- model them explicitly in policy and trust-zone config

## Update Rules

When this document is updated during implementation, record:

- decisions made
- files changed
- tests added or expanded
- benchmark results
- validation commands run
- deviations from this plan
- remaining risks

Append updates chronologically instead of rewriting history.

## Status Log

### 2026-03-16

Initial `strongclaw memory v2` implementation plan created.

Current summary:

- `strongclaw` currently has default-on QMD retrieval plus separate lexical repo context indexing
- upstream OpenClaw already has strong retrieval seams, but not the full research architecture
- the strongest local community plugin is richer than official plugins, but diverges from the Markdown-canonical model
- the best professional path is:
  - preserve OpenClaw compatibility
  - build a separable `strongclaw` memory core
  - ship an opt-in OpenClaw-compatible memory plugin
  - promote only after benchmarked proof

### 2026-03-16 Implementation Update

Implemented the first opt-in `strongclaw memory v2` release without changing the default OpenClaw memory slot.

Decisions made:

- kept the default QMD-backed rollout unchanged
- implemented the v2 engine in Python under `clawops` so it can be tested independently of OpenClaw
- implemented the OpenClaw layer as a thin plugin wrapper that shells out to `clawops memory-v2`
- preserved `memory_search` and `memory_get`
- added `memory_store`, `memory_update`, and `memory_reflect` as additive optional tools
- kept plugin auto-recall and auto-reflect disabled by default

Files changed:

- `src/clawops/memory_v2.py`
- `src/clawops/cli.py`
- `src/clawops/__init__.py`
- `src/clawops/openclaw_config.py`
- `platform/configs/memory/memory-v2.yaml`
- `platform/configs/openclaw/75-strongclaw-memory-v2.example.json5`
- `platform/plugins/strongclaw-memory-v2/*`
- `platform/docs/MEMORY_V2.md`
- `tests/test_memory_v2.py`
- `tests/test_memory_v2_plugin.py`
- `tests/test_openclaw_config.py`
- `tests/test_cli.py`

Tests added or expanded:

- engine config, indexing, search, read, store, update, and reflect coverage
- plugin manifest and tool-surface checks
- overlay rendering checks for the opt-in memory v2 example
- root CLI help coverage for the new command

Validation commands run:

- `PYTHONPATH=src pytest -q`
- `PYTHONPATH=src python3 -m compileall -q src tests`
- `ruff check src/clawops/memory_v2.py src/clawops/cli.py src/clawops/openclaw_config.py tests/test_memory_v2.py tests/test_memory_v2_plugin.py tests/test_openclaw_config.py`
- `pyright src/clawops/memory_v2.py src/clawops/cli.py src/clawops/openclaw_config.py`
- `mypy --follow-imports=skip --ignore-missing-imports src/clawops/memory_v2.py src/clawops/cli.py src/clawops/openclaw_config.py`
- `node --check platform/plugins/strongclaw-memory-v2/index.js`
- `PYTHONPATH=src python3 -m clawops --help`
- `PYTHONPATH=src python3 -m clawops memory-v2 status --json`
- `PYTHONPATH=src python3 -m clawops.openclaw_config --template platform/configs/openclaw/75-strongclaw-memory-v2.example.json5 --repo-root "$(pwd)" --output /tmp/strongclaw-memory-v2-overlay.json`

Deviations from the longer-term plan:

- the first implementation uses SQLite FTS5 only; vector retrieval and ranking benchmarks are still future work
- hook automation is limited to optional auto-recall and auto-reflect in the plugin wrapper
- policy and trust-zone defaults were intentionally left unchanged to avoid expanding write access implicitly

Remaining risks:

- the current v2 engine is retrieval-plus-governed-write capable, but not yet benchmarked against builtin/QMD or `memory-lancedb-pro`
- additive write tools still require explicit trust-zone and policy rollout work before they should be exposed to agents
- plugin runtime depends on the configured `clawops` command being available to the OpenClaw process
