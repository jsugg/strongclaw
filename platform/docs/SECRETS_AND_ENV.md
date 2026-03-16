# Secrets and Environment

## Two-layer model

- **Outer layer:** Varlock for repo-wide env schema and launch-time validation
- **Inner layer:** OpenClaw SecretRefs for runtime binding and reload behavior

## Files

- `platform/configs/varlock/.env.schema`
- `platform/configs/varlock/.env.local.example`
- `platform/examples/openclaw-secretref-*.json5`

## Workflow

1. copy `.env.local.example`
2. fill secrets
3. run `varlock load`
4. launch gateway / sidecars with `varlock run -- ...`

## Rotation

Use `scripts/recovery/rotate_secrets.sh` and the runbook:
`platform/docs/runbooks/credential-rotation.md`
