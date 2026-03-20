# HyperMemory + lossless-claw Current State

Status: supported tier-one path
Owner: Juan Pedro / StrongClaw
Last updated: 2026-03-20

## Purpose

This document records the shipped state of StrongClaw's combined
`lossless-claw` + `strongclaw-memory-v2` runtime.

## Supported product paths

- The default OpenClaw profile still uses the QMD-backed memory overlay.
- `lossless-hypermemory-tier1` is now a first-class setup profile:

  ```bash
  export MEMORY_V2_EMBEDDING_MODEL=openai/text-embedding-3-small
  clawops setup --profile lossless-hypermemory-tier1
  ./scripts/bootstrap/verify_memory_v2_tier1.sh
  clawops doctor
  ```

- The standalone `75-strongclaw-memory-v2.example.json5` overlay remains a
  migration and experimentation surface, not the main supported operator path.

## Tier-one runtime contract

- OpenClaw remains the control plane.
- `lossless-claw` remains the context-engine surface.
- `strongclaw-memory-v2` remains the durable Markdown-canonical memory surface.
- Tier one no longer inherits `40-qmd-context.json5`.
- Tier one renders:
  - `plugins.slots.contextEngine = "lossless-claw"`
  - `plugins.slots.memory = "strongclaw-memory-v2"`
  - `autoRecall: true`
  - `autoReflect: false`
  - `platform/configs/memory/memory-v2.tier1.yaml`

## Memory-v2 state

The shipped `src/clawops/memory_v2` implementation provides:

- canonical storage in `MEMORY.md`, `memory/*.md`, and `bank/**/*.md`
- rebuildable SQLite indexing and governance-aware durable writes
- structured file and `lcm://...` provenance in the derived index and exports
- `qdrant_dense_hybrid` for backward-compatible dense + SQLite lexical search
- `qdrant_sparse_dense_hybrid` for supported tier-one sparse+dense retrieval
- local deterministic BM25-style sparse vector generation
- named Qdrant dense and sparse vector lanes
- tier-one verification that fails on vector-lane breakage or SQLite fallback

Tier-one dense embeddings route through the stable LiteLLM alias
`memory-v2-embedding`, with `MEMORY_V2_EMBEDDING_MODEL` selecting the upstream
embedding model behind that alias.

## Verification surfaces

StrongClaw now verifies this stack through:

- `scripts/bootstrap/doctor_host.sh`
- `scripts/bootstrap/verify_baseline.sh`
- `scripts/bootstrap/verify_memory_v2_tier1.sh`
- `clawops memory-v2 verify-tier1 --json`
- `.github/workflows/memory-plugin-verification.yml`

The plugin workflow covers both:

- the vendored `memory-lancedb-pro` bundle
- the repo-local `strongclaw-memory-v2` bundle through a real OpenClaw host
  path

## Packaging and paths

- The managed `lossless-claw` checkout resolves from StrongClaw app data first,
  then falls back to explicit overrides or repo-local copies.
- StrongClaw runtime artifacts stay in OS-appropriate app data and state
  directories instead of the git checkout.

## Migration bridge

Migration commands remain supported when operators need to export memory-v2
state into `memory-lancedb-pro`:

- `clawops memory migrate-v2-to-pro`
- `clawops memory import-pro-snapshot`
- `clawops memory verify-pro-parity`

Those commands now document migration as a separate bridge, not as the primary
product story for memory-v2.
