# Strongclaw Memory V2

`strongclaw memory v2` is an opt-in memory stack for OpenClaw that keeps Markdown as the source of truth while adding a richer derived index and explicit durable-memory operations.

It does **not** replace the default QMD-backed memory path automatically. The default rendered OpenClaw config still uses `memory-core` plus the QMD backend overlay. Memory v2 only becomes active when you explicitly switch the OpenClaw memory slot to the `strongclaw-memory-v2` plugin.

## Design Goals

- preserve OpenClaw-compatible `memory_search` and `memory_get`
- keep canonical state in Markdown files
- maintain a rebuildable SQLite FTS index
- separate read-side recall from governed durable writes
- keep the rollout opt-in so existing OpenClaw integrations do not change unexpectedly

## Canonical Storage

The engine uses these Markdown surfaces under the configured workspace root:

- `MEMORY.md` or `memory.md`
- `memory/*.md` daily logs
- `bank/world.md`
- `bank/experience.md`
- `bank/opinions.md`
- `bank/entities/*.md`

Daily logs can expose retained entries under a `## Retain` heading. Supported bullets:

- `- Fact: ...`
- `- Reflection: ...`
- `- Opinion[c=0.80]: ...`
- `- Entity[Alice]: ...`

`clawops memory-v2 reflect` promotes those retained entries into the durable `bank/` pages and rebuilds the derived index.

## Derived Index

The derived store lives in SQLite and is rebuilt from Markdown:

- documents table for indexed files
- searchable items table for typed bullets, headings, and paragraphs
- FTS5 virtual table for lexical recall over canonical snippets

Tier One extends the same Markdown-canonical design with optional dense retrieval:

- SQLite remains the source for lexical recall, governance, and canonical provenance
- Qdrant can be enabled as a loopback dense sidecar
- embeddings can target either a local compatible HTTP endpoint or a cloud router

The default shipped config keeps dense retrieval disabled until an operator enables it.

## OpenClaw Compatibility

The opt-in plugin at [platform/plugins/strongclaw-memory-v2](../plugins/strongclaw-memory-v2) preserves the stable OpenClaw memory tool names:

- `memory_search`
- `memory_get`

It also adds gated durable-memory tools:

- `memory_store`
- `memory_update`
- `memory_reflect`

By default, existing trust zones and policy still only expose read-side memory tools. If you want agents to write memory through the plugin, update your OpenClaw trust-zone and policy config explicitly.

The plugin also proxies the `openclaw memory ...` CLI to `clawops memory-v2 ...` when the `strongclaw-memory-v2` slot is active.

## Opt-In Setup

1. Render the example overlay with local paths:

```bash
PYTHONPATH=src python3 -m clawops.openclaw_config \
  --template platform/configs/openclaw/75-strongclaw-memory-v2.example.json5 \
  --repo-root "$(pwd)" \
  --output /tmp/strongclaw-memory-v2.json
```

2. Merge that rendered overlay into your OpenClaw config.

3. Restart the OpenClaw gateway.

4. Verify the slot:

```bash
openclaw plugins list
openclaw memory status --json
```

The shipped example config points the plugin at [platform/configs/memory/memory-v2.yaml](../configs/memory/memory-v2.yaml) and uses the installed `clawops` command.

For the integrated context-engine + memory stack, use the combined overlay:

```bash
PYTHONPATH=src python3 -m clawops.openclaw_config \
  --template platform/configs/openclaw/77-lossless-hypermemory-tier1.example.json5 \
  --repo-root "$(pwd)" \
  --output /tmp/strongclaw-lossless-memory-v2.json
```

## Direct CLI Usage

You can work with the engine directly without enabling the OpenClaw plugin:

```bash
PYTHONPATH=src python3 -m clawops memory-v2 status --json
PYTHONPATH=src python3 -m clawops memory-v2 index --json
PYTHONPATH=src python3 -m clawops memory-v2 search --query "deployment playbook" --json
PYTHONPATH=src python3 -m clawops memory-v2 store --type fact --text "Deploy approvals require two reviewers." --json
PYTHONPATH=src python3 -m clawops memory-v2 reflect --json
```

## Migrating to `memory-lancedb-pro`

Strongclaw now vendors and verifies `memory-lancedb-pro`, but its import CLI
accepts one target scope per run. The `memory-v2` bridge therefore exports a
single scope at a time in the JSON shape that `openclaw memory-pro import`
expects.

1. Rebuild the derived index and promote any retained notes you want to keep as
   durable bank entries:

   ```bash
   PYTHONPATH=src python3 -m clawops memory-v2 reflect --mode safe --json
   ```

2. Export the exact scope you want to migrate:

   ```bash
   clawops memory migrate-v2-to-pro \
     --scope project:strongclaw \
     --output /tmp/strongclaw-memory-pro-project.json
   ```

3. Import that file into the vendored plugin through the ClawOps wrapper. It
   preserves the documented upstream `openclaw memory-pro import` command
   shape and writes an import report alongside the snapshot by default:

   ```bash
   clawops memory import-pro-snapshot \
     --input /tmp/strongclaw-memory-pro-project.json
   ```

   Equivalent upstream command shape:

   ```bash
   openclaw memory-pro import /tmp/strongclaw-memory-pro-project.json \
     --scope project:strongclaw
   ```

By default the bridge exports only top-level memory files and `bank/` entries.
If you intentionally want unreflected retained daily-log notes too, rerun the
export with `--include-daily`.

Before you switch durable writes or deprecate the old overlay, generate a
parity report:

```bash
clawops memory verify-pro-parity \
  --scope project:strongclaw \
  --import-snapshot /tmp/strongclaw-memory-pro-project.json \
  --mode import \
  --query "deployment playbook"
```

If the `openclaw memory-pro search` path is already live on the host, rerun the
same command with `--mode openclaw` or `--mode auto` to compare against the
plugin-backed search surface directly.

## Rollout Notes

- default QMD-backed retrieval remains unchanged
- memory v2 is loaded through `plugins.load.paths` and `plugins.slots.memory`
- auto-recall and auto-reflect exist in the plugin, but both default to `false`
- plugin runtime code is trusted in-process OpenClaw code, so keep the plugin path explicitly controlled
- dense retrieval is opt-in through `platform/configs/memory/memory-v2.yaml`
- the Tier One sidecar surface is `platform/compose/docker-compose.aux-stack.yaml`
- local model operators can layer `platform/compose/docker-compose.ollama.optional.yaml`
