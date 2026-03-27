# Codebase Context Provider

This repo exposes codebase context under the generic `clawops context` namespace.
The first explicit provider is `codebase`, which keeps the SQLite lexical baseline
and adds scale-aware chunk, hybrid, and graph state.

## What it does

- indexes repo files
- builds chunk records for medium and large retrieval
- extracts a lightweight symbol map
- supports lexical search
- syncs dense and sparse chunk vectors into a dedicated Qdrant collection when the hybrid lane is configured and healthy
- reranks the fused candidate pool for medium and large retrieval when rerank providers are configured
- expands dependency context from import edges
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
```

## Included integrations

- provider implementation in `src/clawops/context/codebase/service.py`
- generic provider namespace in `src/clawops/context/`
- built-in OpenClaw QMD memory overlay from `platform/configs/openclaw/40-qmd-context.json5`
- lossless-claw example in `platform/configs/openclaw/70-lossless-context-engine.example.json5`

## Default memory retrieval

The default StrongClaw render path is `hypermemory`, which uses
`lossless-claw` plus `strongclaw-hypermemory`.

The explicit `openclaw-default` fallback profile keeps the OpenClaw built-ins only.

The explicit `openclaw-qmd` fallback profile enables QMD-backed memory
retrieval.

The rendered QMD corpus for `openclaw-qmd` includes:

- `platform/docs`
- `platform/skills`
- repo-root `*.md`
- `platform/workspace/**/*.md`
- optional `repo/upstream/**/*.md` when the upstream checkout exists

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
- `graph.neo4j_url`
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

Symlink handling is explicit:

- `in_repo_only` follows symlinks only when the resolved target stays inside the
  configured repo root
- `never` skips all symlinked files
- `follow` follows all symlinked files and should only be used intentionally

The default shipped policy is `in_repo_only` to prevent context packs from
pulling host files from outside the repo tree.

Reindexing is authoritative for the configured file universe:

- current matching files are inserted or updated
- deleted or newly excluded files are pruned from the lexical and chunk stores

Scale behavior is explicit per invocation:

- `small` keeps the file-level lexical path and avoids graph expansion
- `medium` uses chunk retrieval, hybrid lexical+dense+sparse fusion when the shared embedding and Qdrant sidecars are healthy, and graph expansion that prefers Neo4j with SQLite fallback when allowed
- `large` keeps hybrid and graph expansion enabled and fails closed when Neo4j is unhealthy

For the shipped local sidecar stack, the codebase provider reads Neo4j
credentials from `NEO4J_USERNAME` and `NEO4J_PASSWORD`. The Varlock env
contract now carries those keys directly, and the compose stack derives the
container's `NEO4J_AUTH` value from the same pair so fresh-host bring-up and
graph-backed retrieval stay aligned.

The shipped hybrid lane reuses the same LiteLLM/Qdrant operator contract that backs hypermemory:

- `HYPERMEMORY_EMBEDDING_MODEL`
- `HYPERMEMORY_EMBEDDING_BASE_URL`
- `HYPERMEMORY_QDRANT_URL`
- optional rerank fallback keys `HYPERMEMORY_RERANK_BASE_URL`, `HYPERMEMORY_RERANK_MODEL`, and `HYPERMEMORY_RERANK_API_KEY`
