# Secrets and Environment

## Two-layer model

- **Outer layer:** Varlock for repo-wide env schema and launch-time validation
- **Inner layer:** OpenClaw SecretRefs for runtime binding and reload behavior

## Files

- `platform/configs/varlock/.env.schema`
- `platform/configs/varlock/.env.local.example`
- `platform/examples/openclaw-secretref-*.json5`

## Workflow

1. run `make setup` / `uv run --project . clawops setup` for the guided path, or copy `platform/configs/varlock/.env.local.example` to `platform/configs/varlock/.env.local`
2. fill or review secrets in `platform/configs/varlock/.env.local`
   - provider auth can be stored here with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`
   - optional model selection overrides: `OPENCLAW_DEFAULT_MODEL`, `OPENCLAW_MODEL_FALLBACKS`
   - local-model setups require `OLLAMA_API_KEY=ollama-local` and `OPENCLAW_OLLAMA_MODEL=<pulled-model>`
3. run `./scripts/bootstrap/configure_varlock_env.sh` or `varlock load --path platform/configs/varlock`
4. complete `openclaw configure --section model` during setup, or let `make setup` / `clawops setup` / `./scripts/bootstrap/setup.sh` do it for you
5. launch gateway / sidecars with `varlock run -- ...`

## Rotation

Use `scripts/recovery/rotate_secrets.sh` and the runbook:
`platform/docs/runbooks/credential-rotation.md`
