# Context Service

This repo includes a generic `clawops context` namespace whose first explicit
provider is the local codebase-context service backed by SQLite FTS.

## What it does

- indexes repo files
- extracts a lightweight symbol map
- supports lexical search
- builds stable markdown context packs
- requires an explicit provider and scale per invocation
- keeps markdown memory and docs as source-of-truth
- respects configured include and exclude globs
- skips oversized files by configured size limit

## Why not vector-only

The shipped codebase provider is intentionally auditable and deterministic. QMD
and context-engine plugins are additive, not replacements for disciplined
source material.

## Included integrations

- generic namespace dispatcher in `src/clawops/context/cli.py`
- base lexical codebase provider in `src/clawops/context/codebase/service.py`
- built-in OpenClaw QMD memory overlay from `platform/configs/openclaw/40-qmd-context.json5`
- lossless-claw example in `platform/configs/openclaw/70-lossless-context-engine.example.json5`

## CLI contract

Use the explicit provider form:

- `clawops context codebase index --config platform/configs/context/codebase.yaml --repo . --scale small`
- `clawops context codebase query --config platform/configs/context/codebase.yaml --repo . --scale small --query "jwt"`
- `clawops context codebase pack --config platform/configs/context/codebase.yaml --repo . --scale small --query "jwt" --output /tmp/context-pack.md`

`scale` is now required for direct CLI usage, workflow `context_pack` steps, and
orchestration task context payloads. The current implementation keeps lexical
retrieval across all scales; hybrid retrieval and graph-backed expansion remain
planned follow-up work.

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

The shipped context config supports:

- `index.db_path`
- `index.max_file_size_bytes`
- `index.include_hidden`
- `index.symlink_policy`
- `paths.include`
- `paths.exclude`

The default shipped provider config lives at
`platform/configs/context/codebase.yaml`.

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
