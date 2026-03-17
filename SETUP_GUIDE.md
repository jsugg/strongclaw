# Setup Guide

This guide is the full bring-up order. It assumes macOS 13+ for the first host and a later Linux migration.

## 0. Preconditions

You need:

- Homebrew
- Docker backend (OrbStack preferred on the existing Mac)
- Python 3.12+
- Node 24+ preferred, Node 22.16+ minimum
- a dedicated standard macOS user for the OpenClaw runtime
- a private clone of this repo

## 1. Create the service user

Run:

```bash
sudo ./scripts/bootstrap/create_openclawsvc_macos.sh
```

Then switch into it locally or via loopback SSH:

```bash
ssh openclawsvc@localhost
```

The SSH loopback method is documented in:
`platform/docs/runbooks/macos-service-user-and-ssh.md`

## 2. Clone the repo as the service user

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone <this repo> openclaw-platform-bootstrap
cd openclaw-platform-bootstrap
```

## 3. Install companion tooling

```bash
make dev
make test
```

## 4. Install platform dependencies

```bash
./scripts/bootstrap/bootstrap_host.sh
```

This script verifies or installs:

- `openclaw`
- `acpx`
- `varlock`
- `jq`
- `sqlite`
- `bun`
- Python dependencies
- host-compatible vendored `memory-lancedb-pro` dependencies

## 5. Prepare the Varlock env contract

Copy the example and edit values:

```bash
cp platform/configs/varlock/.env.local.example .env.local
$EDITOR .env.local
```

Validate:

```bash
varlock load
```

## 6. Render the OpenClaw config

```bash
./scripts/bootstrap/render_openclaw_config.sh
```

This writes the merged config to `~/.openclaw/openclaw.json`.

By default it now enables QMD-backed memory retrieval and renders repo-local memory corpus paths for:

- `platform/docs`
- `platform/skills`
- top-level operator guides
- `memory.md`
- `platform/workspace/shared/MEMORY.md`

Use profile rerenders for placeholder-backed variants:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile acp
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local-smart
```

## 7. Install launchd services

```bash
./scripts/bootstrap/install_launchd_services.sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.sidecars.plist
```

Or run manually first:

```bash
./scripts/ops/launch_gateway_with_varlock.sh
./scripts/ops/launch_sidecars_with_varlock.sh
./scripts/bootstrap/verify_sidecars.sh
```

## 8. Verify the secure baseline

```bash
./scripts/bootstrap/verify_baseline.sh
```

Do not continue until all baseline checks are clean.

## 9. Enable ACP workers

```bash
./scripts/bootstrap/bootstrap_acpx.sh
```

This installs the acpx config templates and writes the ACP worker overlay.

Then rerender the OpenClaw config with the ACP worker profile:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile acp
```

Smoke test:

```bash
./scripts/workers/run_codex_session.sh "Summarize the repo"
./scripts/workers/run_claude_review.sh "Review auth boundaries"
```

## 10. Enable repo lexical context indexing and verify QMD

```bash
./scripts/bootstrap/bootstrap_context.sh
./scripts/workers/prewarm_qmd.sh
```

`bootstrap_qmd.sh` is now part of the standard host bootstrap path. Re-run it only if the QMD backend is missing or needs repair.

Index a repo:

```bash
clawops context index \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/openclaw-platform-bootstrap
```

Query it:

```bash
clawops context query \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/openclaw-platform-bootstrap \
  --query "operation journal idempotency"
```

If you need the opt-in local durable memory path instead of the default
QMD-backed retrieval rollout, rerender with:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local
```

Use the Ollama-backed smart extraction profile only after Ollama is serving
both embeddings and a local extraction model:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local-smart
```

If you need a combined placeholder-backed variant, use the root CLI and append
the extra overlay explicitly so every selected fragment is rendered first:

```bash
clawops render-openclaw-config \
  --repo-root "$(pwd)" \
  --profile memory-pro-local \
  --overlay platform/configs/openclaw/20-acp-workers.json5
```

Keep `platform/configs/openclaw/75-strongclaw-memory-v2.example.json5` as the
Markdown-canonical migration source while you validate parity.

## 11. Add channels carefully

### Telegram

1. Put the bot token into `.env.local`.
2. Merge `platform/configs/openclaw/30-channels.json5`.
3. Start the gateway.
4. Approve the first DM via pairing.

```bash
./scripts/bootstrap/enable_telegram.sh
```

Verify the channel overlay, docs, and allowlist contract:

```bash
./scripts/bootstrap/verify_channels.sh
```

### WhatsApp

Use a dedicated number.

```bash
./scripts/bootstrap/enable_whatsapp.sh
```

Re-run channel verification after WhatsApp is enabled:

```bash
./scripts/bootstrap/verify_channels.sh
```

## 12. Enable observability

First start OTEL only:

```bash
./scripts/bootstrap/enable_observability.sh
./scripts/bootstrap/verify_observability.sh
```

Optional: start Langfuse on a separate VM or later on Linux using:

```bash
docker compose -f platform/compose/docker-compose.langfuse.optional.yaml up -d
```

## 13. Keep browser lab separate

Do **not** enable browser automation on the main control-plane host.

On a dedicated box or separate hardened OS user session:

```bash
./scripts/bootstrap/bootstrap_browser_lab.sh
docker compose -f platform/compose/docker-compose.browser-lab.yaml up -d
```

Run exfil tests:

```bash
./scripts/workers/run_browser_lab_exfil_tests.sh
```

Reach the gateway over SSH tunnel only:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Do **not** tunnel browser-lab ports such as `9222` or `3128` to an operator
workstation. Verify the local-only posture after startup:

```bash
./scripts/ops/check_loopback_bindings.sh 18789 3128 9222
```

## 14. Backups and retention

Create and verify a backup:

```bash
./scripts/recovery/backup_create.sh
./scripts/recovery/backup_verify.sh latest
```

Prune old artifacts:

```bash
./scripts/recovery/prune_retention.sh
```

## 15. CI/CD

Push the repo and enable branch protection. The included workflows provide:

- CodeQL
- Semgrep
- Gitleaks
- Trivy
- harness smoke
- nightly regression
- upstream merge gate

## 16. Linux migration

When you move to Linux:

1. install rootless Docker
2. copy `.env` contract and config overlays
3. reuse `platform/systemd/*`
4. move browser lab to a separate runner
5. keep channel ingress private/tailnet-only

See `platform/docs/LINUX_MIGRATION.md`.
