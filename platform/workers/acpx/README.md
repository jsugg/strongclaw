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
acpx codex exec 'Summarize this repository'
acpx claude exec 'Review auth boundaries'
```
