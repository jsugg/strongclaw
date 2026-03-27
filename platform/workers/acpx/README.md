# ACP / acpx workers

This directory contains the external coding worker plane.

## Files

- `global-config.example.json`: template for `~/.acpx/config.json`
- `project-config.example.json`: template for `<repo>/.acpxrc.json`
- `coder-system.md`: system prompt guidance for coding workers
- `reviewer-system.md`: review guidance
- `security-reviewer-system.md`: stricter review guidance

## Install

```bash
npm install -g acpx@0.3.0
acpx config init
cp platform/workers/acpx/global-config.example.json ~/.acpx/config.json
cp platform/workers/acpx/project-config.example.json ~/Projects/strongclaw/repo/upstream/.acpxrc.json
```

## Smoke test

```bash
acpx --approve-reads --format text codex exec 'Summarize this repository'
acpx --approve-all --format json --json-strict claude exec 'Review auth boundaries'
acpx --approve-all --format json --json-strict --model claude-sonnet-4-5 claude exec 'Review auth boundaries'
```

`acpx` resolves config from `~/.acpx/config.json` and `<cwd>/.acpxrc.json`. Strongclaw's adapter
now passes permission mode, output mode, and backend profile explicitly on the command line so
session summaries record the effective execution contract instead of inheriting implicit local
defaults.
