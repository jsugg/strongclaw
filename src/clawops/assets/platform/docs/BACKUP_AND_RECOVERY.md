# Backup and Recovery

## What to back up

- `~/.openclaw`
- repo-local policy/config/state
- harness results you want to keep
- compose state for Postgres / LiteLLM / Qdrant if you need continuity

## Included commands

- `clawops recovery backup-create`
- `clawops recovery backup-verify`
- `clawops recovery restore`
- `clawops recovery prune-retention`
- `clawops recovery rotate-secrets`

## Development-mode repo-local compose state

If you keep compose state under `platform/compose/state` during development, use
the explicit dev wrappers instead of relying on implicit leftover mounts:

- `clawops ops sidecars up --repo-local-state`
- `clawops ops sidecars down --repo-local-state`

`clawops ops sidecars up` owns the LiteLLM schema bootstrap phase. Bring the
stack up through the CLI entrypoint instead of raw `docker compose up` when you
need the supported startup ordering on a cold Postgres state directory.

Prefer targeted cleanup over deleting the whole tree:

- `clawops ops prune-qdrant-test-collections`
- `clawops ops reset-compose-state --component qdrant`
- `clawops ops reset-compose-state --component postgres`

## Recovery order

1. verify the archive
2. restore onto a clean host/user
3. validate env contract
4. restore configs
5. restore sidecars
6. run baseline verification
