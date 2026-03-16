# Context Service

This repo includes a local lexical context service backed by SQLite FTS.

## What it does

- indexes repo files
- extracts a lightweight symbol map
- supports lexical search
- builds stable markdown context packs
- keeps markdown memory and docs as source-of-truth

## Why not vector-only

The service is intentionally auditable and deterministic. QMD and context-engine plugins are additive, not replacements for disciplined source material.

## Included integrations

- base lexical indexer in `src/clawops/context_service.py`
- QMD overlay in `platform/configs/openclaw/40-qmd-context.json5`
- lossless-claw example in `platform/configs/openclaw/70-lossless-context-engine.example.json5`
