# Quickstart

This quickstart gets you from zero to a verified secure baseline.

For host-native runtime-user provisioning and service-manager activation, use [`platform/docs/HOST_PLATFORMS.md`](platform/docs/HOST_PLATFORMS.md) alongside this guide.

## 1. Install the runtime package

```bash
make install
```

`make install` prefers Python `3.12` for the managed environment on supported Darwin/Linux hosts so the default hypermemory rerank stack stays on the most compatible interpreter path. Python `3.13` remains supported when you choose it explicitly.

If you plan to develop on this repo, install `uv` and use `make dev` plus `make test` separately. The companion-tool test entrypoints run through `uv run`, and the bootstrap flow installs `uv` when the host does not already provide it.

If you want shorter commands in an interactive shell, sync the dev environment once and activate `.venv` before running tools directly:

```bash
uv sync --locked
source .venv/bin/activate
pytest -q
deactivate
```

## 2. Prepare the Varlock env contract

You can prepare the env contract either manually or through the guided setup flow. `clawops setup` and `clawops varlock-env configure` create the managed Varlock env under the StrongClaw config dir, repair missing keys, generate required local secrets, and prompt for missing runtime or provider-auth input when needed. On Linux that default path is usually `~/.config/strongclaw/varlock`; on macOS it is usually `~/Library/Application Support/StrongClaw/config/varlock`.

Manual path:

```bash
clawops varlock-env configure --non-interactive
$EDITOR ~/.config/strongclaw/varlock/.env.local
```

Before you continue, decide how OpenClaw should authenticate to an LLM provider. StrongClaw supports two setup paths:

- guided/OpenClaw-managed: `make setup`, `uv run --project . clawops setup`, or `clawops setup` can launch `openclaw configure --section model`
- env-driven: set provider keys plus optional model overrides in the managed `.env.local`
  - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `ZAI_API_KEY`
  - optional `OPENCLAW_DEFAULT_MODEL` and `OPENCLAW_MODEL_FALLBACKS`
  - for local models, set `OLLAMA_API_KEY=ollama-local` and `OPENCLAW_OLLAMA_MODEL=<pulled-model>`
  - a fully local dev baseline should use a pulled Ollama model with at least `16000` context tokens, for example `OPENCLAW_OLLAMA_MODEL=deepseek-r1:latest`

StrongClaw now defaults to `hypermemory`, so set `HYPERMEMORY_EMBEDDING_MODEL=<upstream embedding model>` before you run the no-arg setup path. The hypermemory setup path uses loopback defaults for `HYPERMEMORY_EMBEDDING_BASE_URL` and `HYPERMEMORY_QDRANT_URL` unless you override them.

For a fully local dev stack, `HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text` plus `HYPERMEMORY_EMBEDDING_API_BASE=http://host.docker.internal:11434` is a working local baseline when that Ollama model is already pulled.

If you want the built-in OpenClaw path instead, use the explicit `openclaw-default` profile:

```bash
clawops config memory --set-profile openclaw-default
```

If you want the built-ins plus the experimental QMD backend, use `openclaw-qmd`:

```bash
clawops config memory --set-profile openclaw-qmd
```

## 3. Bring up the host baseline

```bash
make setup
```

Equivalent shell entrypoint:

```bash
clawops setup
```

Equivalent explicit hypermemory path:

```bash
clawops setup --profile hypermemory
clawops hypermemory --config ~/.config/strongclaw/memory/hypermemory.yaml verify
```

Explicit built-in OpenClaw path:

```bash
clawops setup --profile openclaw-default
```

Explicit OpenClaw + QMD path:

```bash
clawops setup --profile openclaw-qmd
```

That setup flow:

- auto-detects the host OS/architecture and dispatches to the compatible bootstrap path
- runs the matching host preflight before attempting package installs
- installs or verifies host package prerequisites
- uses an existing Docker-compatible runtime when one is already installed
- installs Docker only when no Docker-compatible runtime is detected
- fails fast if required installs or the post-bootstrap doctor checks do not pass
- provisions the selected profile's memory and context assets
- installs the vendored `memory-lancedb-pro` dependencies only for the `memory-lancedb-pro` profile
- creates, normalizes, and validates the managed Varlock env contract under the StrongClaw config dir
- prompts for missing Varlock runtime/provider settings when needed, including managed secret backend selection when you want Varlock plugins instead of local `.env` secrets
- configures or validates OpenClaw model/provider auth before services are activated
- renders and activates launchd or systemd service templates
- prepares the hardened OpenClaw config overlays
- prepares sidecar config and service manifests
- runs the baseline verification gate

StrongClaw-generated runtime data does not default into the repository checkout. Setup now places compose state, harness artifacts, ACP summaries, the managed `lossless-claw` checkout, and QMD package files under OS-appropriate app data/state directories instead.

If you intentionally want repo-local compose state during development, keep it explicit instead of relying on stale container mounts:

```bash
clawops ops sidecars up --repo-local-state
clawops ops sidecars down --repo-local-state
clawops ops prune-qdrant-test-collections
clawops ops reset-compose-state --component qdrant
```

You can rerun the host doctor directly after any local change that might affect the rendered config or CLI toolchain:

```bash
clawops doctor-host
```

For the full post-bootstrap readiness sweep, run:

```bash
clawops doctor
```

If Linux bootstrap just added the runtime user to the `docker` group, setup pauses with clear remediation. Open a fresh login shell as that user and rerun the same `make setup` or `clawops setup` command; completed bootstrap work is auto-detected and skipped.

## 4. Rerender the OpenClaw config when you change profiles

```bash
clawops render-openclaw-config
```

This now renders the default StrongClaw profile, `hypermemory`, and writes the result to `~/.openclaw/openclaw.json`.

For the explicit built-in OpenClaw path, render `openclaw-default`, which merges:

- `platform/configs/openclaw/00-baseline.json5`
- `platform/configs/openclaw/10-trust-zones.json5`

For the explicit OpenClaw + QMD path, render `openclaw-qmd`, which adds:

- a rendered form of `platform/configs/openclaw/40-qmd-context.json5`

For placeholder-backed variants, rerender by profile instead of merging raw JSON5 overlays:

```bash
clawops render-openclaw-config --profile openclaw-default
clawops render-openclaw-config --profile openclaw-qmd
clawops render-openclaw-config --profile acp
clawops render-openclaw-config --profile hypermemory
clawops render-openclaw-config --profile memory-lancedb-pro
```

The `openclaw-qmd` profile enables QMD-backed memory retrieval and indexes:

- `platform/docs`
- `platform/skills`
- repo-root `*.md`
- the managed StrongClaw workspace Markdown tree
- the managed upstream checkout when that directory exists

The default `hypermemory` profile enables the combined `lossless-claw` + `strongclaw-hypermemory` runtime, points the plugin at the rendered runtime config under `~/.config/strongclaw/memory/hypermemory.yaml`, enables `autoRecall`, keeps `autoReflect` disabled, and does not inherit the QMD overlay.

## 5. Verify the baseline again on demand

```bash
clawops baseline verify
```

Use `clawops baseline verify --degraded` only when you intentionally need
static or host-only validation and understand that the output is not full
release-readiness evidence.

It runs:

- `openclaw doctor`
- `openclaw security audit --deep`
- `openclaw secrets audit --check`
- `openclaw memory status --deep`
- `openclaw memory search --query "ClawOps" --max-results 1`
- model/provider auth validation
- `clawops verify-platform sidecars`
- `clawops verify-platform observability`
- `clawops verify-platform channels`

## 6. Run devflow

Plan and execute the Strongclaw devflow surface from the repo root:

```bash
clawops devflow plan --goal "Fix regression and add coverage"
clawops devflow run --goal "Fix regression and add coverage" --approved-by operator
clawops devflow status --run-id <run-id>
clawops devflow resume --run-id <run-id> --approved-by operator
clawops devflow audit --run-id <run-id>
```

See [`platform/docs/DEVFLOW.md`](platform/docs/DEVFLOW.md) for the operator run layout, recovery flow, and audit bundle details.
- `clawops verify-platform sidecars`
- `clawops verify-platform observability`
- `clawops verify-platform channels`
- companion-tool smoke tests

For the deeper StrongClaw readiness scan, including model/provider validation and platform verification in one place, run:

```bash
make doctor
```

## 6. Optional staged layers

Add these only in order:

1. ACP workers: `clawops render-openclaw-config --profile acp`
2. Repo codebase context: `clawops context codebase index --scale small --config platform/configs/context/codebase.yaml --repo .`
3. QMD prewarm: `qmd status`
4. Built-in OpenClaw memory fallback:
`clawops setup --profile openclaw-default`
5. Built-in OpenClaw plus experimental QMD:
`clawops setup --profile openclaw-qmd`
6. Opt-in local LanceDB durable memory with Ollama-backed smart extraction by rerendering
`clawops render-openclaw-config --profile memory-lancedb-pro`
7. Migration-only standalone overlay reference:
`platform/configs/openclaw/75-strongclaw-hypermemory.example.json5`
8. Telegram: `platform/docs/channels/telegram.md`
9. WhatsApp: `platform/docs/channels/whatsapp.md`
10. OTel/Langfuse: `clawops verify-platform observability`
11. Browser lab on a separate host: `clawops ops browser-lab up --repo-local-state`

After each layer is enabled, run the matching verification entrypoint:

- ACP workers: `clawops acp-runner --prompt "Summarize the repo"`
- Telegram / WhatsApp: `clawops verify-platform channels`
- OTel/Langfuse: `clawops verify-platform observability`

For remote operator access, tunnel the gateway only:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Do not forward `9222` or `3128`.

## 7. Read the real guide

The quickstart is intentionally narrow. For the actual end-to-end production bring-up, use [`SETUP_GUIDE.md`](SETUP_GUIDE.md).
