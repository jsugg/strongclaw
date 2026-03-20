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
- tier-one `memory-v2` embeddings through the stable `memory-v2-embedding` alias
- centralized budgets / callbacks / fallbacks

## Memory-v2 embedding lane

The supported `lossless-hypermemory-tier1` profile uses:

- `MEMORY_V2_EMBEDDING_MODEL` as the operator-facing upstream embedding model knob
- `memory-v2-embedding` as the stable LiteLLM alias consumed by `memory-v2`
- `MEMORY_V2_EMBEDDING_BASE_URL` to point `memory-v2` at the loopback LiteLLM route

This keeps the tier-one memory config pinned to a stable route while letting
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
