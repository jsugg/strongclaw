# StrongClaw Hypermemory

`hypermemory` is StrongClaw's Markdown-canonical durable memory engine. It is the default StrongClaw memory stack through the `hypermemory` profile, which
binds:

- `plugins.slots.contextEngine = "lossless-claw"`
- `plugins.slots.memory = "strongclaw-hypermemory"`
- `platform/configs/memory/hypermemory.yaml`

The built-in OpenClaw fallback remains available as `openclaw-default`, and the explicit built-ins-plus-QMD fallback remains available as `openclaw-qmd`.

## Design goals

- preserve OpenClaw-compatible `memory_search` and `memory_get`
- keep canonical state in Markdown
- rebuild derived search state from source Markdown
- separate read-side recall from governed durable writes
- keep fallback profiles available for operators who want built-ins only

## Canonical storage

The engine reads these Markdown surfaces under the configured workspace root:

- `MEMORY.md` or `memory.md`
- `memory/*.md` daily logs
- `bank/world.md`
- `bank/experience.md`
- `bank/opinions.md`
- `bank/entities/*.md`

Daily logs can expose retained entries under `## Retain`. Supported bullets:

- `- Fact: ...`
- `- Reflection: ...`
- `- Opinion[c=0.80]: ...`
- `- Entity[Alice]: ...`

`clawops hypermemory reflect` promotes retained entries into the durable `bank/` pages and rebuilds the derived index.

Typed durable entries can also carry evidence metadata. File-backed proof stays in canonical Markdown coordinates and external conversation proof stays as URIs, for example:

- `Fact[evidence=docs/runbook.md#L1-L3|lcm://conversation/abc123/summary/sum_deadbeef]: ...`

The derived index stores those references as structured provenance so export and audit flows can preserve canonical file lines and `lcm://...` links without coupling hypermemory to the context-engine database.

## Derived index

The derived store lives in SQLite and is rebuilt from Markdown:

- `documents` for indexed files
- `search_items` for typed bullets, headings, and paragraphs
- FTS5 for lexical recall over canonical snippets

The supported sparse+dense stack extends that design:

- SQLite stays authoritative for canonical content, governance, provenance, and degraded fallback
- Qdrant stores one named dense vector lane and one named sparse vector lane per point
- sparse vectors are generated locally from normalized retrieval text with a deterministic BM25-style encoder
- dense embeddings use the loopback LiteLLM route configured in [platform/configs/litellm/config.yaml](../configs/litellm/config.yaml)
- [platform/configs/memory/hypermemory.yaml](../configs/memory/hypermemory.yaml) uses `backend.active: qdrant_sparse_dense_hybrid` with `backend.fallback: sqlite_fts`
- [platform/configs/memory/hypermemory.sqlite.yaml](../configs/memory/hypermemory.sqlite.yaml) keeps the engine on pure SQLite FTS

## Missing Markdown behavior

Hypermemory intentionally soft-fails when configured Markdown paths are missing at runtime or during reindex. That matches OpenClaw's own Markdown-memory behavior more closely and avoids breaking the agent because a file was deleted.

- missing corpus roots are surfaced through `status().missingCorpusPaths`
- `reindex` skips unavailable paths instead of raising
- `verify` stays strict and reports missing required corpus roots as errors

That split keeps the runtime robust while preserving an explicit operator check.

## OpenClaw compatibility

The opt-in plugin at [platform/plugins/strongclaw-hypermemory](../plugins/strongclaw-hypermemory) preserves the stable OpenClaw memory tool names:

- `memory_search`
- `memory_get`

It also adds gated durable-memory tools:

- `memory_store`
- `memory_update`
- `memory_reflect`

The plugin proxies `openclaw memory ...` to `clawops hypermemory ...` when the `strongclaw-hypermemory` slot is active.

## Supported setup

Supported default StrongClaw path:

```bash
export HYPERMEMORY_EMBEDDING_MODEL=openai/text-embedding-3-small
clawops setup --profile hypermemory
./scripts/bootstrap/verify_hypermemory.sh
```

That flow renders the default StrongClaw stack with `lossless-claw`, `strongclaw-hypermemory`, `autoRecall: true`, `autoReflect: false`, and [platform/configs/memory/hypermemory.yaml](../configs/memory/hypermemory.yaml).

The hypermemory env contract requires `HYPERMEMORY_EMBEDDING_MODEL`. Guided setup backfills loopback defaults for `HYPERMEMORY_EMBEDDING_BASE_URL` and `HYPERMEMORY_QDRANT_URL` unless you override them.

The shipped hypermemory configs also enable planner-stage reranking. The
primary provider is `local-sentence-transformers` with
`BAAI/bge-reranker-v2-m3`; the fallback is `compatible-http`, which activates
when `HYPERMEMORY_RERANK_BASE_URL` is configured and reachable. If neither
provider is available, search fails open and keeps the provisional hybrid
planner order.

Plain `uv sync` keeps the primary local rerank path on host/Python combinations
with known upstream wheel support: macOS arm64, macOS x86_64 on Python 3.12,
and Linux x86_64 or aarch64/arm64 on Python 3.12 or 3.13. For Raspberry Pi,
that means Raspberry Pi 4/5 with 64-bit Raspberry Pi OS or Ubuntu arm64 stay
on the primary local rerank path. Unsupported combinations such as 32-bit Pi
Linux skip the local dependency and use `compatible-http` or fail-open behavior
instead of blocking setup.

Optional fallback env vars:

- `HYPERMEMORY_RERANK_BASE_URL`
- `HYPERMEMORY_RERANK_MODEL`
- `HYPERMEMORY_RERANK_API_KEY`

To switch profiles later without rerunning guided setup:

```bash
clawops config memory --set-profile openclaw-default
clawops config memory --set-profile openclaw-qmd
clawops config memory --set-profile hypermemory
```

## Standalone overlay setup

1. Render the standalone plugin overlay with local paths:

```bash
PYTHONPATH=src python3 -m clawops.openclaw_config \
  --template platform/configs/openclaw/75-strongclaw-hypermemory.example.json5 \
  --repo-root "$(pwd)" \
  --output /tmp/strongclaw-hypermemory.json
```

2. Merge that overlay into your OpenClaw config.
3. Restart the OpenClaw gateway.
4. Verify the slot:

```bash
openclaw plugins list
openclaw memory status --json
```

The standalone overlay points the plugin at [platform/configs/memory/hypermemory.sqlite.yaml](../configs/memory/hypermemory.sqlite.yaml) and uses the installed `clawops` command.

For the combined context-engine + memory stack, use the integrated overlay:

```bash
PYTHONPATH=src python3 -m clawops.openclaw_config \
  --template platform/configs/openclaw/77-hypermemory.example.json5 \
  --repo-root "$(pwd)" \
  --output /tmp/strongclaw-hypermemory-stack.json
```

## Direct CLI usage

You can work with the engine directly without enabling the OpenClaw plugin:

```bash
PYTHONPATH=src python3 -m clawops hypermemory status --json
PYTHONPATH=src python3 -m clawops hypermemory verify --json --config platform/configs/memory/hypermemory.yaml
PYTHONPATH=src python3 -m clawops hypermemory index --json
PYTHONPATH=src python3 -m clawops hypermemory search --query "deployment playbook" --json
PYTHONPATH=src python3 -m clawops hypermemory store --type fact --text "Deploy approvals require two reviewers." --json
PYTHONPATH=src python3 -m clawops hypermemory reflect --json
```

## Migrating to `memory-lancedb-pro`

StrongClaw vendors and verifies `memory-lancedb-pro`, but its import CLI accepts one scope per run. The hypermemory bridge therefore exports a single scope at a time in the JSON shape that `openclaw memory-pro import` expects.

1. Promote retained notes you want to keep as durable bank entries:

```bash
PYTHONPATH=src python3 -m clawops hypermemory reflect --mode safe --json
```

2. Export the scope you want to migrate:

```bash
clawops memory migrate-hypermemory-to-pro \
  --scope project:strongclaw \
  --output /tmp/strongclaw-memory-pro-project.json
```

3. Import that file into the vendored plugin:

```bash
clawops memory import-pro-snapshot \
  --input /tmp/strongclaw-memory-pro-project.json
```

4. Compare the imported memory-pro results against hypermemory:

```bash
clawops memory verify-pro-parity \
  --scope project:strongclaw \
  --import-snapshot /tmp/strongclaw-memory-pro-project.json \
  --mode openclaw
```

You can also call the upstream import entrypoint directly:

```bash
openclaw memory-pro import /tmp/strongclaw-memory-pro-project.json --scope project:strongclaw
```

## Operational notes

- `openclaw-default` keeps the OpenClaw built-ins available as an explicit fallback
- `openclaw-qmd` keeps the experimental QMD path available as an explicit fallback
- hypermemory is loaded through `plugins.load.paths` and `plugins.slots.memory`
- the default stack uses [platform/compose/docker-compose.aux-stack.yaml](../compose/docker-compose.aux-stack.yaml) for the supporting sidecars

`clawops hypermemory status --json` reports:

- derived-index counts
- active backend and fallback backend
- embedding/rerank provider state
- Qdrant health
- sparse fingerprint state
- missing corpus paths

When `CLAWOPS_STRUCTURED_LOGS=1` is set, hypermemory emits compact JSON lines
for embedding calls, Qdrant search, lexical planning, fusion, rerank, rerank
errors, fallback activation, and vector sync. When OTLP tracing is enabled
through `CLAWOPS_OTEL_ENABLED=1` or the standard `OTEL_EXPORTER_OTLP_*`
variables, the same operations emit spans through the shared ClawOps
observability pipeline, including a dedicated `clawops.hypermemory.rerank`
span.
