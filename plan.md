# HyperMemory + lossless-claw Integration Plan

Status: implemented
Owner: Juan Pedro / StrongClaw
Last updated: 2026-03-18

## 1. What This Document Covers

This document reviews the proposed HyperMemory + lossless-claw design against the current StrongClaw codebase and turns that review into a concrete Tier One implementation plan.

It is intentionally StrongClaw-specific. It does not assume a greenfield memory stack, and it does not assume that upstream OpenClaw defaults can be replaced wholesale without regard for the current StrongClaw architecture.

The plan also keeps the model layer open for both:

- local inference, including Ollama-hosted embedding or extraction models
- cloud inference, preferably routed through the existing LiteLLM sidecar when that is the operationally simpler path

## 2. Current StrongClaw Reality

The current repository already has the right broad split for this work, but the implementation is much narrower than the proposal assumes.

### 2.1 Control-plane and ops-plane boundaries are already correct

StrongClaw explicitly separates:

- OpenClaw as the private loopback-bound control plane
- ACP and sandboxed workers as the execution plane
- sidecars and helper services as the operations plane
- tests and harnesses as the verification plane

That means the proposal is directionally correct to keep OpenClaw thin and move heavy memory/indexing work into ClawOps plus sidecars.

### 2.2 `memory_v2` is already Markdown-canonical, but it is still lexical-only

Today `src/clawops/memory_v2` provides:

- canonical storage in `MEMORY.md`, `memory/*.md`, and `bank/**/*.md`
- a derived SQLite schema in `src/clawops/memory_v2/schema.py`
- lexical retrieval through SQLite FTS5 in `src/clawops/memory_v2/retrieval.py`
- governed write paths through `store`, `update`, and `reflect`

Important current limitations:

- no dense vectors
- no reranker
- no provider abstraction for embeddings or reranking
- no vector backend
- no LCM provenance bridge

### 2.3 The plugin surface is stable, but the recall hook is not where it should be

`platform/plugins/strongclaw-memory-v2/index.js` already preserves the OpenClaw-compatible tool names:

- `memory_search`
- `memory_get`
- optional `memory_store`
- optional `memory_update`
- optional `memory_reflect`

That is the correct compatibility surface and should be preserved.

However, `autoRecall` is currently injected through `before_agent_start`, while the upstream plugin model is better aligned with `before_prompt_build` for context mutation. This is one of the main proposal corrections required for StrongClaw.

### 2.4 The shipped lossless-claw overlay is not yet integrated with `strongclaw-memory-v2`

`platform/configs/openclaw/70-lossless-context-engine.example.json5` still sets:

- `contextEngine = "lossless-claw"`
- `memory = "memory-core"`

`platform/configs/openclaw/75-strongclaw-memory-v2.example.json5` separately sets:

- `memory = "strongclaw-memory-v2"`

That means the current examples do not provide a single first-class integrated profile for lossless-claw plus memory v2 together.

### 2.5 The repo does not currently vendor lossless-claw

The example overlay points at `vendor/lossless-claw`, but that path is not present in the repository. By contrast, StrongClaw already vendors `platform/plugins/memory-lancedb-pro` and has verification around that bundle.

This matters because StrongClaw treats plugins as supply-chain-sensitive code. A production design that depends on lossless-claw needs a pinned acquisition strategy, not just an example path.

### 2.6 StrongClaw already has both cloud and local model patterns

The repo already shows two important operator patterns:

- cloud routing through LiteLLM in `platform/configs/litellm/config.yaml`
- local Ollama-backed plugin configs in `platform/configs/openclaw/75-clawops-memory-pro.local.json5` and `76-clawops-memory-pro.local-smart.json5`

This is the strongest reason not to hard-wire HyperMemory to a single provider family.

## 3. Assessment of the Proposal

## 3.1 What the proposal gets right

The proposal is correct on the core architectural idea:

- lossless-claw should own conversation continuity
- HyperMemory should own durable memory and governed recall
- StrongClaw should keep Markdown canonical storage and derived indexes rebuildable
- the memory plugin should stay thin and ClawOps should own indexing and retrieval logic
- a loopback-only sidecar is consistent with StrongClaw's security and ops model

These align well with the codebase and with StrongClaw's existing platform split.

## 3.2 What needs to be corrected for StrongClaw

### 3.2.1 Do not treat "HyperMemory" as a rename of current surfaces

StrongClaw already exposes:

- CLI: `clawops memory-v2 ...`
- plugin id: `strongclaw-memory-v2`
- docs: `platform/docs/MEMORY_V2.md`

The implementation should evolve `memory_v2` into the proposed architecture without renaming the public surfaces. "HyperMemory" should remain a design label, not a compatibility-breaking package rename.

### 3.2.2 Tier One should not require building both HNSW and Qdrant

The proposal's Tier Zero HNSW path is reasonable in isolation, but if the goal is Tier One, implementing both:

- embedded HNSW
- Qdrant hybrid search

creates duplicated backend work and duplicated operational logic.

For StrongClaw, the lowest-risk Tier One path is:

- keep SQLite FTS5 for sparse/lexical retrieval
- add Qdrant only for dense retrieval
- fuse results in Python first
- move sparse retrieval into Qdrant only later if benchmarks prove it is worth the complexity

This keeps the existing SQLite governance and ranking path useful instead of replacing it prematurely.

### 3.2.3 LCM provenance should be linked, not deeply coupled

The proposal is right to strengthen provenance using lossless-claw summary IDs or message ranges.

But StrongClaw should avoid making `memory_v2` depend directly on the internals of the lossless-claw SQLite schema. The cleaner boundary is:

- store opaque external evidence references in canonical Markdown and `evidence_json`
- use a stable URI-like shape such as `lcm://conversation/<id>/summary/<id>` or `lcm://conversation/<id>/messages/<start>-<end>`
- let OpenClaw and lossless-claw tools resolve those references when needed

This preserves decoupling between the durable memory engine and the context engine's internal storage schema.

### 3.2.4 Server-side sparse vectors in Qdrant should be deferred

Qdrant supports hybrid dense+sparse retrieval, but StrongClaw does not currently have:

- a sparse vector generation pipeline
- a tokenizer/BM25-to-sparse encoding path
- sync semantics for dual sparse+dense point payloads

Implementing that immediately would add more novelty than the current codebase justifies.

Tier One should ship with:

- dense in Qdrant
- sparse in SQLite FTS5
- fusion in ClawOps

Then revisit Qdrant sparse vectors after measurements.

### 3.2.5 Process-spawn removal is not a Tier One requirement

The current plugin shells out to `clawops memory-v2`. That is operationally simple and already matches the repository's wrapper style.

An always-on memory daemon may become valuable later, but it should not be part of the first Tier One integration unless profiling shows that process spawn time is a real contributor to p95 latency.

### 3.2.6 Auto-reflection should remain conservative

StrongClaw already has safe/propose/apply reflection modes. With lossless-claw handling conversation continuity, the need for aggressive automatic durable writes goes down.

The correct default remains:

- `autoRecall = false` or tightly capped
- `autoReflect = false`
- explicit `memory_reflect --mode safe` for promotion

That is better aligned with StrongClaw's governance-first posture.

## 3.3 Benefits of the integrated design

- better token discipline because lossless-claw handles long-history continuity while durable memory injection stays small
- stronger recall quality by combining lexical precision with dense semantic retrieval
- better governance than most plugin-first memory systems because canonical files remain the source of truth
- stronger auditability because durable memory can cite explicit evidence, including conversation-derived references
- cleaner ops posture because Qdrant fits naturally into the existing sidecar model
- model flexibility because embeddings and reranking can target either Ollama/local endpoints or cloud models

## 3.4 Drawbacks and operational costs

- two interacting memory systems increase debugging complexity
- Qdrant adds another persisted service that must be monitored and backed up
- retrieval sync bugs become possible if SQLite and Qdrant drift
- embedding providers introduce network, timeout, quota, and model-drift concerns
- a local/cloud dual-provider strategy needs a disciplined config contract or operators will accumulate one-off settings
- vendoring or pinning lossless-claw adds supply-chain maintenance overhead

## 4. Recommended Target Architecture

## 4.1 Responsibility split

### lossless-claw

Owns:

- message persistence
- conversation compaction
- summary DAG maintenance
- in-conversation recall and drill-down tools

Does not own:

- durable Markdown memory
- governed promotion into bank pages
- long-lived hybrid retrieval over the memory bank and documentation corpus

### `strongclaw-memory-v2` / HyperMemory

Owns:

- canonical durable memory files
- derived SQLite governance state
- dense vector sync
- hybrid retrieval
- optional reranking
- memory promotion workflows

Does not own:

- raw conversation compaction
- replacing the context engine

## 4.2 Retrieval backend strategy for Tier One

Tier One should implement two explicit backends:

- `sqlite_fts`
- `qdrant_dense_hybrid`

The second backend name is intentional. It means:

- lexical candidates still come from SQLite FTS5
- dense candidates come from Qdrant
- fusion happens in ClawOps

This makes the first Tier One release smaller, safer, and easier to benchmark.

## 4.3 Provider abstraction for local and cloud models

This is the most important addition beyond the proposal.

The embedding and reranking layers should use a provider contract that is capability-based, not vendor-based.

Recommended config model:

```yaml
embedding:
  provider: compatible-http
  model: text-embedding-3-small
  base_url: http://127.0.0.1:4000/v1
  api_key_env: LITELLM_MASTER_KEY
  dimensions: 1536
  batch_size: 32
  timeout_ms: 15000

rerank:
  enabled: false
  provider: cross-encoder-local
  model: BAAI/bge-reranker-v2-m3
  timeout_ms: 15000
```

That same contract must support:

- LiteLLM-hosted cloud routing
- direct cloud providers speaking an OpenAI-compatible API
- host-local Ollama embeddings via `http://127.0.0.1:11434/v1`
- future local Python-native providers if needed

Recommended policy:

- cloud defaults route through LiteLLM when an operator already uses the sidecar
- local defaults point directly at Ollama or another host-local endpoint
- model selection is config-only, not code-level branching

## 5. Tier One Implementation Plan

## 5.1 Phase 0: dependency and packaging decisions

Before coding, pin the third-party pieces.

### Required decisions

1. Pin a lossless-claw revision and choose one of:
   - vendor it under `platform/plugins/lossless-claw`
   - or keep it external but require a pinned absolute path placeholder in overlays and docs
2. Add Qdrant as an opt-in sidecar dependency
3. Add Python dependencies for Tier One behind an optional extras group instead of base install

### Dependency note

The current implementation uses the existing `requests` dependency for both:

- compatible HTTP embedding calls
- Qdrant REST calls

That keeps Tier One opt-in without expanding the default dependency surface.

## 5.2 Phase 1: config model expansion

### Files to change

- `src/clawops/memory_v2/models.py`
- `src/clawops/memory_v2/config.py`
- `platform/configs/memory/memory-v2.yaml`

### Add new config dataclasses

Add the following dataclasses to `models.py`:

- `EmbeddingConfig`
- `RerankConfig`
- `HybridConfig`
- `QdrantConfig`
- `InjectionConfig`
- `BackendConfig`

Extend `MemoryV2Config` to include them.

### Parse new YAML sections

Extend `load_config()` in `config.py` to parse and validate:

- `backend`
- `embedding`
- `rerank`
- `hybrid`
- `qdrant`
- `injection`

### Tier One default YAML shape

Update `platform/configs/memory/memory-v2.yaml` to include conservative defaults:

```yaml
backend:
  active: sqlite_fts
  fallback: sqlite_fts

embedding:
  enabled: false
  provider: compatible-http
  model: text-embedding-3-small
  base_url: http://127.0.0.1:4000/v1
  api_key_env: LITELLM_MASTER_KEY
  dimensions: 1536
  batch_size: 32
  timeout_ms: 15000

qdrant:
  enabled: false
  url: http://127.0.0.1:6333
  collection: strongclaw-memory-v2
  timeout_ms: 3000
  prefer_grpc: false

hybrid:
  dense_candidate_pool: 24
  sparse_candidate_pool: 24
  vector_weight: 0.65
  text_weight: 0.35
  fusion: rrf
  rrf_k: 60
  rerank_top_k: 0

injection:
  max_results: 3
  max_chars_per_result: 280
```

Important default policy:

- Tier One code ships dark by default
- enabling Qdrant or embeddings requires explicit config changes

## 5.3 Phase 2: provider and Qdrant clients

### New modules

- `src/clawops/memory_v2/providers.py`
- `src/clawops/memory_v2/qdrant_backend.py`

### `providers.py` responsibilities

- define `EmbeddingProvider` protocol
- define `RerankProvider` protocol
- implement `OpenAICompatibleEmbeddingProvider`
- implement `NoopRerankProvider`
- optionally implement `CrossEncoderRerankProvider` later
- centralize:
  - timeout handling
  - auth header construction
  - batching
  - response validation
  - retry policy for safe idempotent calls

The `OpenAICompatibleEmbeddingProvider` is the key portability layer. It allows:

- LiteLLM for cloud models
- direct OpenAI-compatible cloud endpoints
- Ollama's OpenAI-compatible local surface

Do not create provider branches throughout the engine. Keep provider differences isolated here.

### `qdrant_backend.py` responsibilities

- collection initialization
- point upsert
- point delete for stale documents
- filtered dense search
- health/status probe

Payload fields should include:

- `item_id`
- `rel_path`
- `lane`
- `item_type`
- `scope`
- `source_name`
- `start_line`
- `end_line`
- `modified_at`
- `confidence`

Do not duplicate full snippets in the payload if not needed. Keep payloads compact and read canonical text from SQLite when necessary.

## 5.4 Phase 3: schema and sync metadata

### Files to change

- `src/clawops/memory_v2/schema.py`
- `src/clawops/memory_v2/engine.py`

### Schema additions

Add rebuildable metadata tables:

- `vector_items`
- `backend_state`

Suggested purpose:

- `vector_items`: map `search_item.id` to vector backend metadata
- `backend_state`: record active backend config fingerprint, collection name, embedding model, dimension, and last sync timestamp

Example fields for `vector_items`:

- `item_id INTEGER PRIMARY KEY`
- `backend TEXT NOT NULL`
- `embedding_model TEXT NOT NULL`
- `embedding_dim INTEGER NOT NULL`
- `content_sha256 TEXT NOT NULL`
- `vector_point_id TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### Migration policy

Keep the current "derived state is rebuildable" principle, but stop treating all future backend metadata changes as a blind full reset.

Recommended behavior:

- if schema version changes, rebuild SQLite derived tables
- if backend config fingerprint changes, mark vector sync dirty and trigger a reindex or vector-only resync

This is more robust than the current "drop everything on schema change" pattern once Qdrant is introduced.

## 5.5 Phase 4: reindex pipeline refactor

### Files to change

- `src/clawops/memory_v2/engine.py`
- `src/clawops/memory_v2/parser.py` only if additional evidence metadata formatting is needed

### New `reindex()` phases

Refactor `MemoryV2Engine.reindex()` into explicit phases:

1. discover canonical documents
2. parse Markdown into `IndexedDocument`
3. rebuild SQLite derived rows
4. compute or refresh embeddings for search items eligible for dense retrieval
5. upsert dense vectors into Qdrant when Tier One backend is enabled
6. delete stale vector points for removed items
7. update backend sync metadata

### Important implementation rules

- use document/content hashes so unchanged items do not re-embed unnecessarily
- embed only the normalized recall text, not arbitrary raw blobs
- keep vector sync idempotent
- if Qdrant is unavailable and the active backend is `qdrant_dense_hybrid`, fail loudly in `status` and fall back only if config explicitly allows it

Do not silently degrade without operator visibility.

## 5.6 Phase 5: hybrid retrieval refactor

### Files to change

- `src/clawops/memory_v2/retrieval.py`
- `src/clawops/memory_v2/engine.py`
- `src/clawops/memory_v2/cli.py`

### Retrieval planner changes

Replace the current single-path `search_index()` flow with:

- lexical candidate fetch from SQLite FTS5
- dense candidate fetch from Qdrant
- candidate merge by `item_id` or stable row identity
- fusion using RRF first
- optional rerank over the fused pool
- final diversity pass

### Why RRF first

RRF is more robust than raw weighted score addition across heterogeneous score scales. It is a better first fusion algorithm when combining:

- BM25-derived ranks from SQLite FTS
- dense vector similarity ranks from Qdrant

Weighted score fusion can remain configurable, but RRF should be the default.

### Preserve current ranking semantics where they still matter

Keep the current heuristics after fusion:

- lane weighting
- item type weighting
- confidence boost
- recency boost
- contradiction penalty
- novelty penalty

The current ranking logic is valuable domain logic. It should not be discarded just because dense retrieval is added.

### CLI additions

Extend `clawops memory-v2 search` to expose:

- `--backend`
- `--dense-candidate-pool`
- `--sparse-candidate-pool`
- `--fusion`
- `--rerank-top-k`

These should override config temporarily for debugging and benchmarks.

## 5.7 Phase 6: plugin integration changes

### Files to change

- `platform/plugins/strongclaw-memory-v2/index.js`
- `platform/plugins/strongclaw-memory-v2/openclaw.plugin.json`

### Required plugin changes

1. Move auto-recall from `before_agent_start` to `before_prompt_build`
2. Keep a compatibility fallback only if the target OpenClaw host lacks that hook
3. Continue using the stable memory tool names
4. Keep shell-out execution for Tier One
5. Include the configured backend/provider metadata in tool results where useful

### Prompt injection policy

When auto-recall is enabled:

- inject at most `injection.max_results`
- truncate each snippet to `injection.max_chars_per_result`
- include path and line provenance
- never inject raw large excerpts

lossless-claw already provides a conversation substrate. HyperMemory injection should therefore stay small and precise.

## 5.8 Phase 7: OpenClaw overlays and profile wiring

### Files to change

- `platform/configs/openclaw/70-lossless-context-engine.example.json5`
- `platform/configs/openclaw/75-strongclaw-memory-v2.example.json5`
- add `platform/configs/openclaw/77-lossless-hypermemory-tier1.example.json5`
- `tests/test_openclaw_config.py`

### Recommended overlay strategy

Do not rely on operators to merge two partially conflicting examples mentally.

Add one first-class combined overlay for this stack:

- slot `contextEngine = "lossless-claw"`
- slot `memory = "strongclaw-memory-v2"`
- include both plugin load paths
- point the memory plugin at `platform/configs/memory/memory-v2.yaml`

### Lossless-claw path recommendation

Preferred:

- vendor it under `platform/plugins/lossless-claw`

Fallback:

- keep a placeholder path, but make it explicit in docs that the repository does not currently ship the plugin bundle

## 5.9 Phase 8: compose and operator surfaces

### Files to change

- `platform/compose/docker-compose.aux-stack.yaml`
- optionally add `platform/compose/docker-compose.ollama.optional.yaml`
- operator docs in `README.md`, `QUICKSTART.md`, `SETUP_GUIDE.md`, `platform/docs/MEMORY_V2.md`, and `platform/docs/TOPOLOGIES.md`

### Qdrant service

Add a loopback-only Qdrant sidecar:

```yaml
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - ./state/qdrant:/qdrant/storage
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:6333/healthz >/dev/null 2>&1 || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 10
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
```

### Local model support

Do not make Ollama a hard dependency of Tier One.

Instead:

- document Ollama as an optional local provider
- optionally add a separate compose overlay for operators who want a full local stack
- keep the core Tier One deployment valid with either cloud or local inference

This is the cleanest way to leave the door open without overcommitting the platform to one local runtime.

## 5.10 Phase 9: evidence and provenance bridge

### Files to change

- `src/clawops/memory_v2/engine.py`
- possibly `src/clawops/memory_v2/parser.py`
- docs in `platform/docs/MEMORY_V2.md`

### Required capability

Extend `evidence_json` entries to support external provenance markers such as:

- canonical file evidence
- LCM summary references
- LCM message-range references

Suggested shape:

```json
{
  "kind": "lcm_summary",
  "uri": "lcm://conversation/abc123/summary/sum_deadbeef",
  "relation": "supports"
}
```

This keeps durable memory evidence auditable without forcing `memory_v2` to parse the LCM database directly.

## 5.11 Phase 10: status, diagnostics, and observability

### Files to change

- `src/clawops/memory_v2/engine.py`
- `src/clawops/memory_v2/cli.py`
- docs in `platform/docs/OBSERVABILITY.md`

### Status output should now include

- active backend
- fallback backend
- embedding provider/model
- rerank provider/model
- qdrant enabled/healthy
- vector item count
- vector sync dirty state
- last vector sync timestamp

### Logging and metrics

Add structured logs and counters for:

- embedding calls
- embedding failures
- Qdrant search latency
- lexical search latency
- fusion latency
- rerank latency
- fallback activations

The OTel collector is already part of the platform, so memory-v2 should emit useful signals rather than add a separate observability mechanism.

## 6. Testing Strategy

Tier One needs substantially more verification than the current lexical-only engine.

## 6.1 Unit tests

### Files to add or extend

- `tests/test_memory_v2.py`
- `tests/test_memory_v2_retrieval_hybrid.py`
- `tests/test_memory_v2_config.py`
- `tests/test_memory_v2_qdrant_backend.py`
- `tests/test_memory_v2_plugin.py`
- `tests/test_openclaw_config.py`

### Unit cases

- config parsing for local and cloud embedding providers
- provider request formatting for OpenAI-compatible endpoints
- provider response validation and bad-response handling
- hybrid fusion correctness for:
  - lexical-only hits
  - dense-only hits
  - overlapping hits
- RRF ordering stability
- scope filtering across both lexical and dense candidate sets
- contradiction penalties surviving fusion
- injection truncation behavior
- plugin auto-recall hook wiring using `before_prompt_build`

## 6.2 Integration tests

### Add a small Qdrant-backed test slice

Use either:

- a real Qdrant container in CI for a dedicated integration job
- or a minimal test harness gated behind an environment flag locally

Integration cases:

- `reindex()` creates the collection and upserts points
- stale documents are removed from Qdrant on reindex
- `search()` returns fused hits with correct scope filtering
- `status()` surfaces Qdrant health correctly
- fallback behavior is explicit when Qdrant is unavailable

## 6.3 Regression tests

Protect current semantics:

- Markdown remains canonical
- `reflect()` still writes governed proposals correctly
- `memory_get` reads canonical files, not vector payloads
- lexical-only mode still works when embeddings are disabled

## 6.4 Benchmark fixtures

Extend `platform/configs/memory/benchmarks/strongclaw-memory-v2.yaml` with cases that measure:

- exact token retrieval
- semantic paraphrase retrieval
- scope isolation
- recency preference
- contradiction demotion

Track:

- Recall@k
- nDCG@k
- end-to-end search latency
- time spent in lexical, dense, fusion, and rerank stages

## 6.5 CI changes

Add or extend CI jobs so Tier One verification includes:

- pure unit tests without Qdrant
- integration tests with Qdrant
- plugin/config rendering tests
- optional docs parity updates if operator docs change

Do not make Ollama-backed tests part of the required CI path. Those should remain optional local validation.

## 7. Rollout Plan

### Stage 1

- ship config and code with backend defaulting to `sqlite_fts`
- no behavior change for current operators

### Stage 2

- enable Qdrant in a dev profile
- keep auto-recall and auto-reflect off
- benchmark lexical versus hybrid results

### Stage 3

- enable the combined lossless-claw + memory-v2 overlay
- verify token budgets and recall quality

### Stage 4

- enable constrained auto-recall only if prompt-budget metrics look healthy

### Stage 5

- consider reranking only after the basic dense+lexical path is stable

## 8. Final Recommendation

The proposal is strong in overall direction, but it should be adapted to StrongClaw as follows:

1. Keep `memory_v2` and `strongclaw-memory-v2` as the public surfaces.
2. Use lossless-claw for conversation continuity and durable memory-v2 for curated long-term knowledge.
3. Make Tier One Qdrant dense-first, not dense+sparse-in-Qdrant on day one.
4. Preserve SQLite FTS and the current ranking/governance logic.
5. Add a provider abstraction that treats LiteLLM, direct cloud endpoints, and Ollama as interchangeable OpenAI-compatible transport targets.
6. Move recall injection to `before_prompt_build`.
7. Ship one integrated overlay for the combined stack instead of separate partially conflicting examples.
8. Treat lossless-claw packaging as a real supply-chain decision, not just a config example.

If implemented this way, StrongClaw gets the benefits of the proposal without taking on unnecessary churn or backend duplication.
