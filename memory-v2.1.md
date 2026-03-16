# Strongclaw Memory V2.1 Plan

This document is the living implementation tracker for the next iteration of `strongclaw memory v2`.

`memory v2` is now implemented as an opt-in, OpenClaw-compatible, Markdown-canonical memory system. `memory v2.1` is the next phase: improve retrieval quality, memory structure, governance, and reflection so that `strongclaw` can plausibly outperform the best local OpenClaw memory stacks without breaking OpenClaw integration.

This document should be updated after each meaningful design, implementation, benchmark, or validation milestone.

## Purpose

- record the current shipped `memory v2` baseline
- capture why `memory v2.1` is needed
- compare `strongclaw memory v2` against the strongest local alternative currently identified
- define the compatibility constraints that must not be broken
- describe the target `v2.1` architecture and phased implementation plan
- define validation gates and promotion criteria

## Current Baseline

The repository already ships a compatibility-safe `memory v2` foundation:

- Python core engine:
  - `src/clawops/memory_v2.py`
- CLI integration:
  - `src/clawops/cli.py`
- overlay rendering helpers:
  - `src/clawops/openclaw_config.py`
- shipped config:
  - `platform/configs/memory/memory-v2.yaml`
- opt-in OpenClaw plugin:
  - `platform/plugins/strongclaw-memory-v2/`
- opt-in overlay:
  - `platform/configs/openclaw/75-strongclaw-memory-v2.example.json5`
- operator docs:
  - `platform/docs/MEMORY_V2.md`

Current `memory v2` behavior:

- preserves OpenClaw-compatible `memory_search`
- preserves OpenClaw-compatible `memory_get`
- adds optional:
  - `memory_store`
  - `memory_update`
  - `memory_reflect`
- keeps canonical memory in Markdown and bank pages
- uses a rebuildable SQLite FTS5 index
- remains opt-in and does not replace the default QMD-backed OpenClaw memory path

Current `memory v2` limitations:

- retrieval is still FTS5-first and relatively simple
- no benchmark harness yet against builtin/QMD/community stacks
- no dual-lane retrieval planner
- no typed evidence graph
- no contradiction model
- no scoped confidence-aware ranking
- reflection is useful but still basic
- no benchmarked proof that it beats the best local community plugin

## Why V2.1 Exists

`memory v2` is a safe architecture and compatibility milestone. It is not yet the best available local memory stack.

The next serious goal is:

- keep the OpenClaw-safe, Markdown-canonical design
- keep the current opt-in rollout and compatibility invariants
- materially improve retrieval quality and durable memory semantics beyond current official and community options

## Reference Comparison

### OpenClaw official baseline

Upstream OpenClaw currently provides:

- builtin memory with a derived SQLite store, FTS, optional vector support, hybrid retrieval, MMR, and temporal decay
- QMD as a stronger local retrieval backend for Markdown corpora
- a stable bundled memory tool contract centered on:
  - `memory_search`
  - `memory_get`

Important implication:

- `strongclaw` should keep those stable tool names and response expectations if it wants to remain OpenClaw-safe

### Strongest local community reference

The strongest local community implementation currently identified is `memory-lancedb-pro`.

What it appears to do better today:

- stronger hybrid retrieval and ranking
- richer auto-capture and extraction
- scope handling
- lifecycle/tiering concepts
- more operational memory features as a finished product

What `strongclaw memory v2` already does better:

- keeps Markdown and bank pages canonical
- stays aligned with OpenClaw's published research direction
- preserves OpenClaw-compatible `memory_search` / `memory_get`
- keeps rollout additive and compatibility-safe
- is easier to audit in Git and easier to rebuild from source-of-truth files

### Honest current comparison

Today:

- retrieval/product maturity: `memory-lancedb-pro` is ahead
- OpenClaw compatibility and rollout safety: `strongclaw memory v2` is ahead
- Markdown-canonical architecture: `strongclaw memory v2` is ahead

Therefore `memory v2.1` should not try to copy `memory-lancedb-pro` into a DB-first design. It should improve retrieval and durable memory quality while preserving the architectural advantages that `strongclaw` already has.

## V2.1 Goal

Build a `strongclaw memory v2.1` that is:

- better than `memory-lancedb-pro` on `strongclaw`'s actual workload
- better aligned with OpenClaw's long-term memory research architecture
- still safe to integrate into OpenClaw without breaking existing configurations or trust boundaries

This means `v2.1` must combine:

- repo-wide corpus retrieval
- durable typed memory retrieval
- explicit evidence and contradiction modeling
- governed writes and scoped visibility
- explainable ranking
- benchmarked proof of quality

## Non-Negotiable Compatibility Invariants

Every phase of implementation must preserve these boundaries unless explicitly revisited and documented.

### OpenClaw tool contract

- preserve `memory_search`
- preserve `memory_get`
- keep result shapes compatible with current OpenClaw callers

### OpenClaw config contract

- do not break `plugins.slots.memory`
- do not break `memory.backend`
- do not break current trust-zone tool names
- do not break current workflow command shapes

### OpenClaw integration model

- keep the plugin opt-in until promotion is justified
- use official plugin and hook seams rather than patching OpenClaw core
- keep current QMD retrieval path available throughout the rollout

### Markdown-canonical architecture

- keep Markdown and bank pages as source of truth
- keep the derived DB/index rebuildable
- never make the database the only authoritative source

### Strongclaw policy and safety model

- do not widen write permissions by default
- keep read-only agents read-only unless configuration explicitly opts in
- preserve current default OpenClaw memory behavior for operators who do nothing

## Design Principles

`memory v2.1` should improve the system on the axes where `memory-lancedb-pro` is strong, while remaining faithful to the architecture that makes `strongclaw` distinct.

Core principles:

- Markdown is canonical
- the index is derived and disposable
- corpus retrieval and durable memory retrieval are separate lanes
- durable writes are explicit, scoped, and auditable
- retrieval quality must be benchmarked, not assumed
- degraded modes must be visible, not silent

## Target Architecture Delta

`memory v2.1` should evolve `memory v2` into a modular system with these major pieces.

### 1. Modular core package

Refactor the current single-module implementation into a package that separates:

- config loading
- Markdown parsing
- bank-page parsing
- schema management
- index persistence
- retrieval planning
- reflection
- governance and scope enforcement
- CLI surfaces

Target package direction:

```text
src/clawops/memory_v2/
  __init__.py
  config.py
  parser.py
  schema.py
  store.py
  retrieval.py
  reflection.py
  governance.py
  cli.py
```

Compatibility note:

- keep `clawops memory-v2 ...` stable while refactoring internally

### 2. Dual-lane retrieval

Implement two retrieval lanes:

- corpus lane
  - docs
  - runbooks
  - skills
  - guides
  - operator memory docs
- durable memory lane
  - retained facts
  - typed bank pages
  - reflections
  - entities
  - opinions

Recommended direction:

- keep FTS5 as the universal lexical baseline
- reuse QMD for corpus-lane semantic retrieval where it is the better fit
- add richer ranking and fusion above the retrieval lanes rather than duplicating corpus search engines unnecessarily

Compatibility note:

- preserve `memory_search` output semantics while improving ranking under the hood

### 3. Typed memory model

Expand the derived store beyond documents and snippets.

Target structures:

- `documents`
- `chunks`
- `facts`
- `entities`
- `opinions`
- `reflections`
- `evidence_links`
- `conflicts`
- `scopes`

Each durable memory item should be able to carry:

- source path
- source line range when available
- scope
- confidence
- timestamps
- supporting evidence
- contradicting evidence
- related entities

Compatibility note:

- keep canonical content in Markdown and bank pages; these typed tables are derived, not authoritative

### 4. Better ranking and explainability

Add a retrieval planner that can combine:

- lexical scores
- semantic scores
- scope relevance
- recency
- confidence
- contradiction penalties
- diversity/MMR-style deduping

Also add internal explainability surfaces so the system can answer:

- why this result ranked highly
- which evidence supports it
- whether it is contradicted
- what scope it came from

Compatibility note:

- richer metadata should be additive; do not break current `memory_search` consumers

### 5. Proposal-driven capture and reflection

Instead of copying aggressive auto-capture behavior from community plugins, implement a safer retain/reflect loop:

- session or lifecycle events produce candidate facts, opinions, and entity updates
- candidates are deduped and scored
- candidate writes become proposals with evidence
- approved or allowed proposals are reflected into canonical Markdown and bank pages

Recommended direction:

- automatic commits may be acceptable for agent-private scope
- shared/project/global scope writes should remain policy-gated

Compatibility note:

- additive write tools must remain opt-in and policy-controlled

### 6. Scope and governance

Support explicit visibility and write control for:

- `agent:<id>`
- `project:<id>`
- `user:<id>`
- `global`

Requirements:

- retrieval must respect scope visibility
- writes must be auditable
- shared-scope writes must remain governed

Compatibility note:

- do not widen trust-zone defaults until policy and operator docs are updated intentionally

## Recommended Implementation Plan

### Phase 0. Freeze compatibility and build the benchmark harness

Goals:

- write a clear compatibility contract for:
  - `memory_search`
  - `memory_get`
  - current plugin config shape
  - current overlay behavior
- define the `strongclaw` benchmark query set
- define quality metrics:
  - recall@k
  - citation correctness
  - stale-memory suppression
  - false-positive recall rate
  - latency

Deliverables:

- compatibility contract document
- golden-query benchmark fixtures
- benchmark runner covering:
  - OpenClaw builtin memory
  - QMD
  - `strongclaw memory v2`
  - `memory-lancedb-pro`

OpenClaw safety gate:

- do not change runtime behavior in this phase

### Phase 1. Refactor `memory v2` into a modular core

Goals:

- break `src/clawops/memory_v2.py` into maintainable components
- preserve current CLI behavior and tests
- isolate stable boundaries for future ranking and reflection work

Deliverables:

- new package layout
- unchanged CLI contract
- unchanged plugin behavior

OpenClaw safety gate:

- `clawops memory-v2` commands and current plugin config must remain compatible

### Phase 2. Implement the typed derived schema

Goals:

- add facts, entities, opinions, reflections, evidence links, conflicts, and scopes to the derived model
- keep rebuilds deterministic from Markdown and session artifacts

Deliverables:

- schema migration plan
- rebuild-from-source tests
- typed query helpers

OpenClaw safety gate:

- existing `memory_search` and `memory_get` outputs must still work on top of the evolved store

### Phase 3. Implement dual-lane retrieval

Goals:

- separate corpus retrieval from durable memory retrieval
- integrate corpus-lane results and durable-memory results through a retrieval planner

Recommended direction:

- keep QMD available for corpus retrieval
- keep local-only operation
- avoid duplicating infrastructure unless there is benchmarked value

Deliverables:

- retrieval planner
- score fusion
- citation-preserving result assembly

OpenClaw safety gate:

- stable tool outputs preserved
- fallback/degraded mode is explicit in status, not silently hidden

### Phase 4. Add ranking improvements and explainability

Goals:

- improve relevance with:
  - fusion
  - MMR/deduping
  - recency weighting
  - confidence weighting
  - contradiction penalties
- add explainability metadata and internal diagnostics

Deliverables:

- ranking policy module
- explanation surfaces
- benchmark improvements over the current baseline

OpenClaw safety gate:

- explanation is additive
- no incompatible tool output change by default

### Phase 5. Add proposal-driven capture and governed writes

Goals:

- upgrade `memory_store`, `memory_update`, and `memory_reflect`
- introduce proposal objects and reviewable reflection flow
- keep canonical writes in Markdown and bank pages

Deliverables:

- proposal schema
- governed write path
- reviewable reflection output

OpenClaw safety gate:

- write tools remain opt-in
- read-only users and agents remain read-only by default

### Phase 6. Add scope-aware policy enforcement

Goals:

- implement scope visibility enforcement
- gate shared/project/global writes
- keep auditability for mutations

Deliverables:

- scope policy checks
- mutation audit trail
- negative tests for unauthorized writes

OpenClaw safety gate:

- no trust-zone default expansion without explicit config and docs changes

### Phase 7. Evaluate and decide on promotion

Goals:

- compare `v2.1` against builtin, QMD, and `memory-lancedb-pro`
- review operator complexity and rollback behavior
- decide whether to:
  - keep `v2.1` opt-in
  - promote `v2.1` to default
  - keep QMD as default and use `v2.1` for selected profiles only

Promotion criteria:

- better measured retrieval quality on `strongclaw` workloads
- correct citations
- no OpenClaw compatibility regressions
- acceptable operational burden

OpenClaw safety gate:

- default promotion happens only after compatibility, policy, and benchmark proof

## What We Need To Beat `memory-lancedb-pro`

`memory-lancedb-pro` is strongest where it behaves like a finished long-term-memory product. `strongclaw` should beat it with a different combination of strengths, not by becoming a LanceDB-first clone.

The differentiators should be:

- Markdown-canonical source of truth
- better OpenClaw compatibility
- repo-wide corpus retrieval plus durable memory retrieval in one coherent stack
- explicit evidence and contradiction tracking
- governed and auditable writes
- benchmarked quality on real `strongclaw` workloads

If `v2.1` only adds more features without improving these axes, it will not be a meaningful step forward.

## Validation Matrix

Minimum required validation before default-promotion discussion:

- compatibility tests for:
  - `memory_search`
  - `memory_get`
  - plugin config shape
  - overlay rendering
  - current OpenClaw workflow surfaces
- rebuild tests proving the derived store is reconstructible from canonical Markdown
- migration tests from current `memory v2` content and current QMD-accessible corpus
- benchmark comparisons against:
  - builtin OpenClaw memory
  - QMD
  - `memory-lancedb-pro`
  - current `strongclaw memory v2`
- negative tests proving unauthorized writes are rejected
- evidence integrity tests
- contradiction-handling tests
- degraded-mode tests proving failures are surfaced clearly

## Immediate Next Steps

The next implementation pass should start here:

1. freeze the compatibility contract and benchmark plan
2. refactor `src/clawops/memory_v2.py` into a package without changing behavior
3. define the typed derived schema and rebuild tests
4. build the dual-lane retrieval planner behind the existing `memory_search` surface

Do not start by widening write permissions or changing the default OpenClaw memory slot.

## Open Questions

These should be resolved during implementation:

- should corpus-lane semantic retrieval reuse QMD directly, or should `strongclaw` own more of that layer?
- should the first governed write scope be `agent:<id>` only, or include `project:<id>`?
- should contradictions block reflection automatically, or only reduce confidence and require review?
- should explainability remain internal first, or should `memory_explain` become a public additive tool?
- what benchmark query set best reflects `strongclaw` operator and agent workflows?

## Status Log

### 2026-03-16

- `memory v2` is already implemented as an opt-in, OpenClaw-compatible base.
- `memory v2.1` is defined as the next step focused on retrieval quality, typed memory structure, governed reflection, and benchmarked superiority over current local alternatives.
- This tracker was created to guide that work and to ensure each phase preserves OpenClaw integration boundaries.
