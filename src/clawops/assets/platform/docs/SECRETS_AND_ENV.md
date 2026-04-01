# Secrets and Environment

## Two-layer model

- **Outer layer:** Varlock for repo-wide env schema and launch-time validation
- **Inner layer:** OpenClaw SecretRefs for runtime binding and reload behavior

## Files

- managed Varlock env dir: `~/.config/strongclaw/varlock` on Linux,
`~/Library/Application Support/StrongClaw/config/varlock` on macOS
- source template assets: `platform/configs/varlock/.env.schema`,
`platform/configs/varlock/.env.local.example`
- managed plugin overlay: `.env.plugins` (generated locally when you choose a managed secret backend)
- `platform/examples/openclaw-secretref-*.json5`

## Workflow

1. run `make setup` / `uv run --project . clawops setup`, or create the managed env contract with `clawops varlock-env configure`
2. choose where provider and integration secrets should live
   - local `.env.local`
   - or a supported Varlock backend: 1Password, AWS Secrets Manager, AWS Parameter Store, Azure Key Vault, Bitwarden, Google Secret Manager, or Infisical
3. fill or review secrets in the managed `.env.local`
   - provider auth can be stored here with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`
   - optional model selection overrides: `OPENCLAW_DEFAULT_MODEL`, `OPENCLAW_MODEL_FALLBACKS`
   - local-model setups require `OLLAMA_API_KEY=ollama-local` and `OPENCLAW_OLLAMA_MODEL=<pulled-model>`
   - a fully local dev baseline should use a pulled Ollama model with at least `16000` context tokens, for example `OPENCLAW_OLLAMA_MODEL=deepseek-r1:latest`
   - hypermemory requires `HYPERMEMORY_EMBEDDING_MODEL`
   - for a fully local hypermemory baseline, use `HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text`
   - local LiteLLM-to-Ollama routing also needs `HYPERMEMORY_EMBEDDING_API_BASE=http://host.docker.internal:11434`
   - hypermemory defaults `HYPERMEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:4000/v1`
   - hypermemory defaults `HYPERMEMORY_QDRANT_URL=http://127.0.0.1:6333`
4. if you chose a managed backend, let guided setup generate `.env.plugins`, or maintain that file manually for hybrid setups
5. run `clawops varlock-env configure` or `varlock load --path ~/.config/strongclaw/varlock`
6. complete `openclaw configure --section model` during setup, or let `make setup` / `clawops setup` / `clawops setup` do it for you
7. launch gateway / sidecars with `varlock run -- ...`

## Backend notes

- StrongClaw keeps core machine-local secrets such as the gateway token and LiteLLM bootstrap secrets in `.env.local` by default.
- Managed backends are primarily used for LLM provider credentials and similar integration tokens.
- `HYPERMEMORY_EMBEDDING_MODEL` is the only hypermemory env key that
normally needs operator input; the loopback base URLs are backfilled by guided setup unless you override them.
- `.env.plugins` is ignored by git and imported from `.env.schema`, so manual and guided setup can be mixed safely. If you author it by hand, keep explicit plugin version specifiers in each `@plugin(...)` decorator.

## Rotation

Use `clawops recovery rotate-secrets` and the runbook: `platform/docs/runbooks/credential-rotation.md`
