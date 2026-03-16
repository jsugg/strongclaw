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
- installs `openclaw`, `acpx`, and `varlock`
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

and writes the result to `~/.openclaw/openclaw.json`.

## 4. Start sidecars

```bash
./scripts/bootstrap/bootstrap_sidecars.sh
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
- Docker/compose health checks
- companion-tool smoke tests

## 6. Optional staged layers

Add these only in order:

1. ACP workers: `./scripts/bootstrap/bootstrap_acpx.sh`
2. Repo context service: `./scripts/bootstrap/bootstrap_context.sh`
3. QMD backend: `./scripts/bootstrap/bootstrap_qmd.sh`
4. Telegram: `./scripts/bootstrap/enable_telegram.sh`
5. WhatsApp: `./scripts/bootstrap/enable_whatsapp.sh`
6. OTel/Langfuse: `./scripts/bootstrap/enable_observability.sh`
7. Browser lab on a separate host: `./scripts/bootstrap/bootstrap_browser_lab.sh`

## 7. Read the real guide

The quickstart is intentionally narrow. For the actual end-to-end production bring-up, use [`SETUP_GUIDE.md`](SETUP_GUIDE.md).
