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

The current implementation is intentionally Markdown-canonical and local-only. It does not depend on remote embeddings or a vector service.

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

## Direct CLI Usage

You can work with the engine directly without enabling the OpenClaw plugin:

```bash
PYTHONPATH=src python3 -m clawops memory-v2 status --json
PYTHONPATH=src python3 -m clawops memory-v2 index --json
PYTHONPATH=src python3 -m clawops memory-v2 search --query "deployment playbook" --json
PYTHONPATH=src python3 -m clawops memory-v2 store --type fact --text "Deploy approvals require two reviewers." --json
PYTHONPATH=src python3 -m clawops memory-v2 reflect --json
```

## Rollout Notes

- default QMD-backed retrieval remains unchanged
- memory v2 is loaded through `plugins.load.paths` and `plugins.slots.memory`
- auto-recall and auto-reflect exist in the plugin, but both default to `false`
- plugin runtime code is trusted in-process OpenClaw code, so keep the plugin path explicitly controlled
