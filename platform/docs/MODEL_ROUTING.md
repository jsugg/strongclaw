# Model Routing

## Direct subscription lanes

Keep these direct inside OpenClaw:
- OpenAI Codex OAuth
- GitHub Copilot device login
- Qwen portal OAuth

## API-key lanes through LiteLLM

Use LiteLLM for:
- OpenRouter
- Z.AI
- Moonshot
- hypermemory `hypermemory` embeddings through the stable `hypermemory-embedding` alias
- centralized budgets / callbacks / fallbacks

## Memory-v2 embedding lane

The supported `hypermemory` profile uses:

- `HYPERMEMORY_EMBEDDING_MODEL` as the operator-facing upstream embedding model knob
- `hypermemory-embedding` as the stable LiteLLM alias consumed by `hypermemory`
- `HYPERMEMORY_EMBEDDING_BASE_URL` to point `hypermemory` at the loopback LiteLLM route

This keeps the hypermemory memory config pinned to a stable route while letting
operators swap the upstream embedding model behind that alias.

## Role defaults

- reader -> cheap, read-only
- coder -> strong coding lane
- reviewer -> separate family if possible
- messaging -> cheap and low-blast-radius

## Budgeting

The included LiteLLM config ships:
- `default_team_settings`
- routing aliases
- fallbacks
- OTel callbacks

Tune these after measuring real workloads.
