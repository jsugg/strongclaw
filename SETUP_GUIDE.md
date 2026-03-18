# Setup Guide

This guide is the full bring-up order for either a macOS or Linux operator host.

## 0. Preconditions

You need:

- a private clone of this repo
- Python 3.12+
- Node 24+ preferred, Node 22.16+ minimum
- a dedicated non-admin runtime user for OpenClaw
- a supported package manager for the host bootstrap path
- either an already installed Docker-compatible runtime or permission to install Docker as the fallback runtime

Host-specific notes:

- macOS: Homebrew plus either a Docker-compatible runtime such as OrbStack, Rancher Desktop, Colima, or Docker Desktop, or permission for bootstrap to install Docker Desktop as the fallback runtime
- Linux: `apt-get`, `sudo`, `curl`, and either a Docker-compatible runtime that exposes `docker compose` for the runtime user, or permission for bootstrap to install Docker Engine as the fallback runtime

## 1. Provision the runtime user

```bash
sudo ./scripts/bootstrap/create_openclawsvc.sh
```

Then switch into the runtime account with the host-native path.

macOS:

```bash
ssh openclawsvc@localhost
```

Linux:

```bash
sudo -iu openclawsvc
```

Runbooks:

- macOS: `platform/docs/runbooks/macos-service-user-and-ssh.md`
- Linux: `platform/docs/runbooks/linux-runtime-user-and-systemd.md`

## 2. Clone the repo as the runtime user

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone <this repo> strongclaw
cd strongclaw
```

## 3. Install the runtime package

```bash
make install
```

If you plan to develop on this repo, install `uv` and then use:

```bash
make dev
make test
```

`make test` runs in the locked managed environment via `uv run`. The baseline
verifier also uses that managed test path, and bootstrap installs `uv` when
the host does not already provide it.

## 4. Prepare the Varlock env contract

Copy the example and edit values in the Varlock config directory that the launch
wrappers use:

```bash
cp platform/configs/varlock/.env.local.example platform/configs/varlock/.env.local
$EDITOR platform/configs/varlock/.env.local
```

If `varlock` is already installed on the host, you can validate the contract now:

```bash
varlock load --path platform/configs/varlock
```

## 5. Preferred baseline bring-up

```bash
./scripts/bootstrap/install.sh
```

That path bootstraps the host, validates the repo-local Varlock env contract,
renders or refreshes the host service definitions, activates the gateway plus
sidecars, and runs the baseline verification gate.

The bootstrap flow verifies or installs:

- `openclaw`
- `acpx`
- `varlock`
- `jq`
- `sqlite`
- `bun`
- Python dependencies
- host-compatible vendored `memory-lancedb-pro` dependencies

For container backends, bootstrap first looks for an existing Docker-compatible
runtime that already exposes `docker` plus `docker compose`. If it finds one,
Strongclaw uses it and does not install Docker over it. If it finds an
alternative runtime without the Docker CLI integration enabled yet, bootstrap
stops and tells you to finish that integration instead of replacing it.
Only when no Docker-compatible runtime is detected does bootstrap install
Docker as the fallback runtime.

If Linux bootstrap just added the runtime user to the `docker` group, start a
fresh `sudo -iu openclawsvc` shell before rerunning:

```bash
./scripts/bootstrap/install.sh --skip-bootstrap
```

If you need a placeholder-backed profile during bring-up, rerender through the
wrapper:

```bash
./scripts/bootstrap/install.sh --profile acp
./scripts/bootstrap/install.sh --profile memory-pro-local
./scripts/bootstrap/install.sh --profile memory-pro-local-smart
```

Use `./scripts/bootstrap/doctor_host.sh` again after any host-side package or
config change that might affect the local OpenClaw runtime contract.

## 6. Manual config and service flow

If you want to control the render, service activation, or verification steps
separately, use the lower-level entrypoints directly.

Bootstrap the host:

```bash
./scripts/bootstrap/bootstrap.sh
```

Then validate the env contract and render the OpenClaw config:

```bash
varlock load --path platform/configs/varlock
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

Install and activate services:

```bash
./scripts/bootstrap/install_host_services.sh --activate
```

Equivalent manual activation commands:

macOS:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.sidecars.plist
```

Linux:

```bash
systemctl --user daemon-reload
systemctl --user enable --now openclaw-sidecars.service
systemctl --user enable --now openclaw-gateway.service
```

Or run the gateway and sidecars manually first:

```bash
./scripts/ops/launch_gateway_with_varlock.sh
./scripts/ops/launch_sidecars_with_varlock.sh
./scripts/bootstrap/verify_sidecars.sh
```

## 7. Verify the secure baseline

If you used `./scripts/bootstrap/install.sh`, this verification already
ran. Re-run it directly whenever you want to recheck the host baseline:

```bash
./scripts/bootstrap/verify_baseline.sh
```

Do not continue until all baseline checks are clean.

## 8. Enable ACP workers

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

## 9. Enable repo lexical context indexing and verify QMD

```bash
./scripts/bootstrap/bootstrap_context.sh
./scripts/workers/prewarm_qmd.sh
```

`bootstrap_qmd.sh` is now part of the standard host bootstrap path. Re-run it only if the QMD backend is missing or needs repair.

Index a repo:

```bash
clawops context index \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/strongclaw
```

Query it:

```bash
clawops context query \
  --config platform/configs/context/context-service.yaml \
  --repo ~/Projects/strongclaw \
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

1. Put the bot token into `platform/configs/varlock/.env.local`.
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

Optional: start Langfuse on a separate VM or separate sidecar host using:

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

## 16. Linux host notes

When you run on Linux:

1. prefer rootless Docker or a locked-down `docker` group for the runtime user
2. render user units with `./scripts/bootstrap/install_host_services.sh`
3. keep browser lab on a separate runner
4. keep channel ingress private or tailnet-only

See `platform/docs/HOST_PLATFORMS.md`.
