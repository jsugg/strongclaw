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
- centralized budgets / callbacks / fallbacks

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
