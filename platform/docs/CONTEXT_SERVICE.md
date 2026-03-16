# Context Service

This repo includes a local lexical context service backed by SQLite FTS.

## What it does

- indexes repo files
- extracts a lightweight symbol map
- supports lexical search
- builds stable markdown context packs
- keeps markdown memory and docs as source-of-truth
- respects configured include and exclude globs
- skips oversized files by configured size limit

## Why not vector-only

The service is intentionally auditable and deterministic. QMD and context-engine plugins are additive, not replacements for disciplined source material.

## Included integrations

- base lexical indexer in `src/clawops/context_service.py`
- default rendered QMD memory overlay from `platform/configs/openclaw/40-qmd-context.json5`
- lossless-claw example in `platform/configs/openclaw/70-lossless-context-engine.example.json5`

## Default memory retrieval

The baseline OpenClaw render path now enables QMD-backed memory retrieval by default.

The rendered QMD corpus includes:

- `platform/docs`
- `platform/skills`
- top-level operator guides
- `memory.md`
- `platform/workspace/shared/MEMORY.md`

This is retrieval-only by default. The project does not currently expose a writable memory tool contract.

## Config contract

The shipped context config supports:

- `index.db_path`
- `index.max_file_size_bytes`
- `index.include_hidden`
- `index.symlink_policy`
- `paths.include`
- `paths.exclude`

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
- deleted or newly excluded files are pruned from the lexical store
