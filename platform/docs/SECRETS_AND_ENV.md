# Secrets and Environment

## Two-layer model

- **Outer layer:** Varlock for repo-wide env schema and launch-time validation
- **Inner layer:** OpenClaw SecretRefs for runtime binding and reload behavior

## Files

- `platform/configs/varlock/.env.schema`
- `platform/configs/varlock/.env.local.example`
- `platform/configs/varlock/.env.plugins` (generated locally when you choose a managed secret backend)
- `platform/examples/openclaw-secretref-*.json5`

## Workflow

1. run `make setup` / `uv run --project . clawops setup` for the guided path, or copy `platform/configs/varlock/.env.local.example` to `platform/configs/varlock/.env.local`
2. choose where provider and integration secrets should live
   - local `.env.local`
   - or a supported Varlock backend: 1Password, AWS Secrets Manager, AWS Parameter Store, Azure Key Vault, Bitwarden, Google Secret Manager, or Infisical
3. fill or review secrets in `platform/configs/varlock/.env.local`
   - provider auth can be stored here with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`
   - optional model selection overrides: `OPENCLAW_DEFAULT_MODEL`, `OPENCLAW_MODEL_FALLBACKS`
   - local-model setups require `OLLAMA_API_KEY=ollama-local` and `OPENCLAW_OLLAMA_MODEL=<pulled-model>`
4. if you chose a managed backend, let guided setup generate `platform/configs/varlock/.env.plugins`, or maintain that file manually for hybrid setups
5. run `./scripts/bootstrap/configure_varlock_env.sh` or `varlock load --path platform/configs/varlock`
6. complete `openclaw configure --section model` during setup, or let `make setup` / `clawops setup` / `./scripts/bootstrap/setup.sh` do it for you
7. launch gateway / sidecars with `varlock run -- ...`

## Backend notes

- StrongClaw keeps core machine-local secrets such as the gateway token and LiteLLM bootstrap secrets in `.env.local` by default.
- Managed backends are primarily used for LLM provider credentials and similar integration tokens.
- `.env.plugins` is ignored by git and imported from `.env.schema`, so manual and guided setup can be mixed safely. If you author it by hand, keep explicit plugin version specifiers in each `@plugin(...)` decorator.

## Rotation

Use `scripts/recovery/rotate_secrets.sh` and the runbook:
`platform/docs/runbooks/credential-rotation.md`
