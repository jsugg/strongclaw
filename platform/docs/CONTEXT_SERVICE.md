# Codebase Context Provider

This repo exposes codebase context under the generic `clawops context` namespace. The first explicit provider is `codebase`, which keeps the SQLite lexical baseline and adds scale-aware chunk, hybrid, and graph state.

## What it does

- indexes repo files
- builds tree-sitter-first chunk records for supported code languages and falls back to the heuristic chunker when grammars are unavailable
- extracts a lightweight symbol map
- supports lexical search
- updates lexical, chunk, and graph state inline during reindexing
- lets `clawops context codebase worker` consolidate dense and sparse chunk vectors into a dedicated Qdrant collection when the hybrid lane is configured and healthy
- reranks the fused candidate pool for medium and large retrieval when rerank providers are configured
- expands dependency context from import edges plus symbol-level define/call/reference edges
- builds stable markdown context packs with provider and scale metadata
- keeps markdown memory and docs as source-of-truth
- respects configured include and exclude globs
- skips oversized files by configured size limit

## Why not vector-only

The service is intentionally auditable and deterministic. Hybrid retrieval augments lexical search; it does not replace disciplined source material.

## CLI surface

```bash
clawops context codebase index --scale small --config platform/configs/context/codebase.yaml --repo .
clawops context codebase query --scale medium --config platform/configs/context/codebase.yaml --repo . --query "context request"
clawops context codebase pack --scale medium --config platform/configs/context/codebase.yaml --repo . --query "workflow runner" --output /tmp/context-pack.md
clawops context codebase worker --scale medium --config platform/configs/context/codebase.yaml --repo . --once
clawops context codebase benchmark --scale medium --config platform/configs/context/codebase.yaml --repo . --fixtures platform/configs/context/benchmarks/codebase.yaml --json
```

The shipped config keeps default indexing focused on repo-authored material by excluding large vendored trees and the benchmark fixture directory.

## Included integrations

- provider implementation in `src/clawops/context/codebase/service.py`
- generic provider namespace in `src/clawops/context/`
- built-in OpenClaw QMD memory overlay from `platform/configs/openclaw/40-qmd-context.json5`
- lossless-claw example in `platform/configs/openclaw/70-lossless-context-engine.example.json5`

## Default memory retrieval

The default StrongClaw render path is `hypermemory`, which uses `lossless-claw` plus `strongclaw-hypermemory`.

The explicit `openclaw-default` fallback profile keeps the OpenClaw built-ins only.

The explicit `openclaw-qmd` fallback profile enables QMD-backed memory retrieval.

The rendered QMD corpus for `openclaw-qmd` includes:

- `platform/docs`
- `platform/skills`
- repo-root `*.md`
- the managed StrongClaw workspace Markdown tree
- the managed upstream checkout when it exists

This is retrieval-only by default. The project does not currently expose a writable memory tool contract.

## Config contract

The shipped codebase config supports:

- `index.db_path`
- `index.max_file_size_bytes`
- `index.include_hidden`
- `index.symlink_policy`
- `paths.include`
- `paths.exclude`
- `graph.enabled`
- `graph.backend`
- `graph.allow_degraded_fallback`
- `graph.neo4j_url` (`bolt://127.0.0.1:7687` by default; legacy HTTP URLs are normalized for the driver)
- `graph.neo4j_username_env`
- `graph.neo4j_password_env`
- `graph.database`
- `graph.depth`
- `graph.limit`
- `embedding.enabled`
- `embedding.provider`
- `embedding.model`
- `embedding.base_url`
- `embedding.api_key_env`
- `embedding.api_key`
- `embedding.dimensions`
- `embedding.batch_size`
- `embedding.timeout_ms`
- `rerank.enabled`
- `rerank.provider`
- `rerank.fallback_provider`
- `rerank.fail_open`
- `rerank.normalize_scores`
- `rerank.local.model`
- `rerank.local.batch_size`
- `rerank.local.max_length`
- `rerank.local.device`
- `rerank.compatible_http.model`
- `rerank.compatible_http.base_url`
- `rerank.compatible_http.api_key_env`
- `rerank.compatible_http.api_key`
- `rerank.compatible_http.timeout_ms`
- `hybrid.dense_candidate_pool`
- `hybrid.sparse_candidate_pool`
- `hybrid.vector_weight`
- `hybrid.text_weight`
- `hybrid.fusion`
- `hybrid.rrf_k`
- `hybrid.rerank_candidate_pool`
- `qdrant.enabled`
- `qdrant.url`
- `qdrant.collection`
- `qdrant.dense_vector_name`
- `qdrant.sparse_vector_name`
- `qdrant.timeout_ms`
- `qdrant.api_key_env`
- `qdrant.api_key`

Path filters are applied to repo-relative POSIX paths before indexing.

The shipped config excludes packaged runtime mirrors under `src/clawops/assets/**` so retrieval ranks the real source tree instead of duplicated install assets.

Symlink handling is explicit:

- `in_repo_only` follows symlinks only when the resolved target stays inside the
configured repo root
- `never` skips all symlinked files
- `follow` follows all symlinked files and should only be used intentionally

The default shipped policy is `in_repo_only` to prevent context packs from pulling host files from outside the repo tree.

Reindexing is authoritative for the configured file universe:

- current matching files are inserted or updated
- deleted or newly excluded files are pruned from the lexical and chunk stores

Scale behavior is explicit per invocation:

- `small` keeps the file-level lexical path and avoids graph expansion
- `medium` uses syntax-aware chunk retrieval, hybrid lexical+dense+sparse fusion when the shared embedding and Qdrant sidecars are healthy, and graph expansion that prefers Neo4j with SQLite fallback when allowed
- `large` keeps hybrid and graph expansion enabled and fails closed when Neo4j is unhealthy

Hybrid runtime artifacts are intentionally deferred for medium and large reindexing:

- `index` refreshes lexical, chunk, and edge state synchronously
- `worker` reconciles pending Qdrant point deletions and chunk-vector upserts in the background
- `query` and `pack` transparently fall back to lexical chunk retrieval until the worker finishes a healthy hybrid sync

Successful hybrid batches now persist incrementally, so a late local embedding timeout leaves the worker degraded but does not discard already indexed chunk vectors. The next worker or benchmark pass resumes from the remaining chunks.

When chunk hashes and pending deletions already match the indexed corpus, the worker reuses the persisted sparse metadata and skips both sparse rebuild and dense re-embedding work. On cold syncs, the sparse lane now normalizes each chunk once and reuses that token stream for both corpus statistics and the persisted sparse vector payloads.

The Neo4j lane now uses the official Python driver over the Bolt protocol and keeps the SQLite graph fallback aligned by materializing symbol-aware `DEFINES`, `CALLS`, and `REFERENCES` edges alongside file import edges.

## Benchmark fixtures

Use `clawops context codebase benchmark` to measure Recall@k and MRR against a curated fixture file. The command reindexes the repo, consolidates runtime artifacts, and then evaluates each case against the final context surface for the current provider scale: the top retrieved roots plus graph dependency expansion when that lane is active. When the fixture file lives under the repo root, the benchmark command excludes that exact file from indexing so the query set cannot self-match.

Fixture files live under `platform/configs/context/benchmarks/` and accept:

- `name`
- `query`
- `maxResults`
- `expectedPaths`
- `expectedChunkIds`

Each case must define at least one expected path or chunk id.

Benchmark authoring guidance:

- For `small`, benchmark cases should use exact lexical or symbol-oriented queries.
- semantic or paraphrase-oriented benchmark cases should target `medium` or `large`.
- Keep the shipped benchmark CLI example on `--scale medium` when validating hybrid recall expectations.
- The shipped local medium config keeps embedding batches conservative and allows longer HTTP timeouts because direct Ollama embedding can still exceed optimistic defaults on singleton retries.

For the shipped local sidecar stack, the codebase provider reads Neo4j credentials from `NEO4J_USERNAME` and `NEO4J_PASSWORD`. The Varlock env contract now carries those keys directly, and the compose stack derives the container's `NEO4J_AUTH` value from the same pair so fresh-host bring-up and graph-backed retrieval stay aligned.

The shipped local medium lane uses direct Ollama embeddings plus the shared Qdrant and rerank contract:

- `HYPERMEMORY_EMBEDDING_MODEL` with a pulled local Ollama embedding model such as `ollama/nomic-embed-text`
- direct Ollama on `http://127.0.0.1:11434`
- `HYPERMEMORY_QDRANT_URL`
- optional rerank fallback keys `HYPERMEMORY_RERANK_BASE_URL`, `HYPERMEMORY_RERANK_MODEL`, and `HYPERMEMORY_RERANK_API_KEY`

If you want the codebase provider to route embeddings through LiteLLM or a remote OpenAI-compatible gateway instead, switch `embedding.provider` back to `compatible-http` and point `embedding.base_url` at that endpoint.
