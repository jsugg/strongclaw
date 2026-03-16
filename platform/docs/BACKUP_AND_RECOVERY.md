# Backup and Recovery

## What to back up

- `~/.openclaw`
- repo-local policy/config/state
- harness results you want to keep
- compose state for Postgres / LiteLLM if you need continuity

## Included scripts

- `scripts/recovery/backup_create.sh`
- `scripts/recovery/backup_verify.sh`
- `scripts/recovery/restore_openclaw.sh`
- `scripts/recovery/prune_retention.sh`
- `scripts/recovery/rotate_secrets.sh`

## Recovery order

1. verify the archive
2. restore onto a clean host/user
3. validate env contract
4. restore configs
5. restore sidecars
6. run baseline verification
