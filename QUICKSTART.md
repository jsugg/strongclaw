# Quickstart

This quickstart gets you from zero to a verified secure baseline.

For host-native runtime-user provisioning and service-manager activation, use
[`platform/docs/HOST_PLATFORMS.md`](platform/docs/HOST_PLATFORMS.md) alongside
this guide.

## 1. Install the runtime package

```bash
make install
```

If you plan to develop on this repo, install `uv` and use `make dev` plus
`make test` separately. The companion-tool test entrypoints run through
`uv run`, and the bootstrap flow installs `uv` when the host does not
already provide it.

## 2. Prepare the Varlock env contract

```bash
cp platform/configs/varlock/.env.local.example platform/configs/varlock/.env.local
$EDITOR platform/configs/varlock/.env.local
```

## 3. Bring up the host baseline

```bash
./scripts/bootstrap/install.sh
```

That script:

- auto-detects the host OS/architecture and dispatches to the compatible bootstrap path
- runs the matching host preflight before attempting package installs
- creates the repo-local directories under `platform/`
- installs or verifies host package prerequisites
- uses an existing Docker-compatible runtime when one is already installed
- installs Docker only when no Docker-compatible runtime is detected
- fails fast if required installs or the post-bootstrap doctor checks do not pass
- provisions the default QMD semantic memory backend
- installs the vendored `memory-lancedb-pro` dependencies with a host-compatible LanceDB version
- validates the repo-local Varlock env contract under `platform/configs/varlock`
- renders and activates launchd or systemd service templates
- prepares the hardened OpenClaw config overlays
- prepares sidecar config and service manifests
- runs the baseline verification gate

You can rerun the host doctor directly after any local change that might affect
the rendered config or CLI toolchain:

```bash
./scripts/bootstrap/doctor_host.sh
```

If Linux bootstrap just added the runtime user to the `docker` group, start a
fresh login shell as that user and rerun:

```bash
./scripts/bootstrap/install.sh --skip-bootstrap
```

## 4. Rerender the OpenClaw config when you change profiles

```bash
./scripts/bootstrap/render_openclaw_config.sh
```

This renders the `default` profile, which merges:

- `platform/configs/openclaw/00-baseline.json5`
- `platform/configs/openclaw/10-trust-zones.json5`
- a rendered form of `platform/configs/openclaw/40-qmd-context.json5`

and writes the result to `~/.openclaw/openclaw.json`.

For placeholder-backed variants, rerender by profile instead of merging raw
JSON5 overlays:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile acp
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local-smart
```

The rendered config enables QMD-backed memory retrieval by default and indexes:

- `platform/docs`
- `platform/skills`
- top-level operator guides
- `memory.md`
- `platform/workspace/shared/MEMORY.md`

## 5. Verify the baseline again on demand

```bash
./scripts/bootstrap/verify_baseline.sh
```

It runs:

- `openclaw doctor`
- `openclaw security audit --deep`
- `openclaw secrets audit --check`
- `openclaw memory status --deep`
- `openclaw memory search --query "ClawOps" --max-results 1`
- `./scripts/bootstrap/verify_sidecars.sh --skip-runtime`
- `./scripts/bootstrap/verify_observability.sh --skip-runtime`
- `./scripts/bootstrap/verify_channels.sh`
- companion-tool smoke tests

## 6. Optional staged layers

Add these only in order:

1. ACP workers: `./scripts/bootstrap/bootstrap_acpx.sh`
2. Repo context service: `./scripts/bootstrap/bootstrap_context.sh`
3. QMD prewarm: `./scripts/workers/prewarm_qmd.sh`
4. Opt-in local LanceDB durable memory after the default QMD flow is stable by rerendering
   `./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local`
5. Optional local smart extraction profile with Ollama-backed LLM extraction by rerendering
   `./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local-smart`
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
