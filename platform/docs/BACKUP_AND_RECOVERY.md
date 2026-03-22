# Backup and Recovery

## What to back up

- `~/.openclaw`
- repo-local policy/config/state
- harness results you want to keep
- compose state for Postgres / LiteLLM / Qdrant if you need continuity

## Included scripts

- `scripts/recovery/backup_create.sh`
- `scripts/recovery/backup_verify.sh`
- `scripts/recovery/restore_openclaw.sh`
- `scripts/recovery/prune_retention.sh`
- `scripts/recovery/rotate_secrets.sh`

## Development-mode repo-local compose state

If you keep compose state under `platform/compose/state` during development, use
the explicit dev wrappers instead of relying on implicit leftover mounts:

- `./scripts/ops/launch_sidecars_dev.sh`
- `./scripts/ops/stop_sidecars_dev.sh`

Prefer targeted cleanup over deleting the whole tree:

- `./scripts/ops/prune_qdrant_test_collections.sh`
- `./scripts/ops/reset_dev_compose_state.sh --component qdrant`
- `./scripts/ops/reset_dev_compose_state.sh --component postgres`

## Recovery order

1. verify the archive
2. restore onto a clean host/user
3. validate env contract
4. restore configs
5. restore sidecars
6. run baseline verification
