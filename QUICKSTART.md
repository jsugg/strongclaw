# Quickstart

This quickstart gets you from zero to a verified secure baseline.

## 1. Install the repo helper package

```bash
python3 -m pip install -e .
make test
```

## 2. Bootstrap the host

```bash
./scripts/bootstrap/bootstrap_macos.sh
```

That script:

- creates the repo-local directories under `platform/`
- installs or verifies Homebrew prerequisites
- attempts a best-effort install of `openclaw`, `acpx`, and `varlock`
- provisions the default QMD semantic memory backend
- renders launchd templates
- prepares the hardened OpenClaw config overlays
- prepares sidecar config and service manifests

## 3. Render and install the OpenClaw config

```bash
./scripts/bootstrap/render_openclaw_config.sh
```

This merges:

- `platform/configs/openclaw/00-baseline.json5`
- `platform/configs/openclaw/10-trust-zones.json5`
- a rendered form of `platform/configs/openclaw/40-qmd-context.json5`

and writes the result to `~/.openclaw/openclaw.json`.

The rendered config enables QMD-backed memory retrieval by default and indexes:

- `platform/docs`
- `platform/skills`
- top-level operator guides
- `memory.md`
- `platform/workspace/shared/MEMORY.md`

## 4. Start sidecars

```bash
./scripts/bootstrap/bootstrap_sidecars.sh
./scripts/bootstrap/verify_sidecars.sh
```

This starts:

- Postgres
- LiteLLM
- OpenTelemetry Collector

## 5. Verify the baseline

```bash
./scripts/bootstrap/verify_baseline.sh
```

It runs:

- `openclaw doctor`
- `openclaw security audit --deep`
- `openclaw secrets audit --check`
- `openclaw memory status --deep`
- `openclaw memory search --query "OpenClaw Platform Bootstrap" --max-results 1`
- `./scripts/bootstrap/verify_sidecars.sh --skip-runtime`
- `./scripts/bootstrap/verify_observability.sh --skip-runtime`
- `./scripts/bootstrap/verify_channels.sh`
- companion-tool smoke tests

## 6. Optional staged layers

Add these only in order:

1. ACP workers: `./scripts/bootstrap/bootstrap_acpx.sh`
2. Repo context service: `./scripts/bootstrap/bootstrap_context.sh`
3. QMD prewarm: `./scripts/workers/prewarm_qmd.sh`
4. Opt-in local LanceDB durable memory after the default QMD flow is stable by merging
   `platform/configs/openclaw/75-clawops-memory-pro.local.json5`
5. Optional local smart extraction profile with Ollama-backed LLM extraction:
   `platform/configs/openclaw/76-clawops-memory-pro.local-smart.json5`
6. Keep `platform/configs/openclaw/75-strongclaw-memory-v2.example.json5` only as a
   migration-source/reference overlay while you validate parity
7. Telegram: `./scripts/bootstrap/enable_telegram.sh`
8. WhatsApp: `./scripts/bootstrap/enable_whatsapp.sh`
9. OTel/Langfuse: `./scripts/bootstrap/enable_observability.sh`
10. Browser lab on a separate host: `./scripts/bootstrap/bootstrap_browser_lab.sh`

After each layer is enabled, run the matching verification entrypoint:

- ACP workers: `./scripts/workers/run_codex_session.sh "Summarize the repo"`
- Telegram / WhatsApp: `./scripts/bootstrap/verify_channels.sh`
- OTel/Langfuse: `./scripts/bootstrap/verify_observability.sh`

For remote operator access, tunnel the gateway only:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Do not forward `9222` or `3128`.

## 7. Read the real guide

The quickstart is intentionally narrow. For the actual end-to-end production bring-up, use [`SETUP_GUIDE.md`](SETUP_GUIDE.md).
