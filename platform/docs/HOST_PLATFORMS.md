# Host Platforms

Strongclaw supports two operator-host platforms:

- macOS hosts using Homebrew plus `launchd`
- Linux hosts using `apt-get` plus user-level `systemd`

Both use the same bootstrap entrypoints, config overlays, and verification
gates.

The bootstrap contract is runtime-aware: if the host already has a
Docker-compatible runtime that exposes `docker` plus `docker compose`,
Strongclaw reuses it. Docker is installed only as the fallback runtime when no
compatible backend is detected.

## Compatibility matrix

StrongClaw's supported baseline is derived from the codebase constraints plus
the pinned external tools that setup installs.

| Component | Supported / pinned version | Why |
| --- | --- | --- |
| Python | `3.12`, `3.13` | `pyproject.toml` requires `>=3.12`, and Ruff, Black, mypy, and Pyright all target Python 3.12 syntax/features. |
| Node.js | `22.16.x`, `24.x` | OpenClaw `2026.3.13` requires `>=22.16.0`; ACPX `0.3.0`, Varlock `0.5.0`, and QMD `2.0.1` all require Node 22+. |
| `uv` | `0.10.9` | Setup and CI pin this version for reproducible environment sync. |
| Varlock | `0.5.0` | Setup installs and verifies this exact version. |
| OpenClaw | `2026.3.13` | Setup installs this exact CLI version. |
| ACPX | `0.3.0` | Setup installs this exact CLI version. |
| QMD | `2.0.1` | Setup installs this exact package version behind the `~/.bun/bin/qmd` wrapper. |
| `lossless-claw` | `v0.3.0` | Setup installs this exact git ref for the context-engine plugin. |
| Hypermemory local rerank | macOS `arm64` on Python `3.12`/`3.13` with `torch==2.8.0`; macOS `x86_64` on Python `3.12` with `torch==2.2.2`; Linux `x86_64` and `aarch64`/`arm64` on Python `3.12`/`3.13` with `torch==2.8.0`, including Raspberry Pi 4/5 with 64-bit Raspberry Pi OS or Ubuntu | Upstream `torch` wheel coverage is narrower than the base project matrix. Unsupported combinations skip the local `sentence-transformers` dependency and fall back to `compatible-http` or fail-open search order. |

CI enforces this support statement through:

- a Python matrix on `3.12` and `3.13`
- a setup/install smoke matrix on Node `22.16.0` and `24.13.1`
- a vendored memory-plugin integration matrix on Node `22.16.0` and `24.13.1`
- a `strongclaw-hypermemory` OpenClaw host-functional lane on Ubuntu

For low-end or older hosts, this split matters:

- x86_64 Linux laptops stay on the default local rerank path
- Apple Silicon Macs stay on the default local rerank path for Python `3.12` and `3.13`
- Intel Macs use a compatibility pin for local rerank on Python `3.12`
- Apple Silicon Macs and supported Linux hosts use the pinned `torch==2.8.0` local rerank path on Python `3.12` and `3.13`
- Raspberry Pi 4/5 running 64-bit Raspberry Pi OS or Ubuntu arm64 stay on the default local rerank path
- 32-bit Raspberry Pi Linux hosts skip the local rerank dependency and should use `compatible-http` if reranking is required

## Runtime data locations

StrongClaw-generated runtime artifacts should not live inside the repository
checkout. The setup, doctor, harness, ACP runner, workflow context-pack, and
compose helper scripts now default to OS-appropriate app directories instead.

| Kind | Linux default | macOS default |
| --- | --- | --- |
| StrongClaw data | `~/.local/share/strongclaw` | `~/Library/Application Support/StrongClaw` |
| StrongClaw state | `~/.local/state/strongclaw` | `~/Library/Application Support/StrongClaw/state` |
| StrongClaw logs | `~/.local/state/strongclaw/logs` | `~/Library/Logs/StrongClaw` |
| Compose sidecar state | `<state>/compose` | `<state>/compose` |
| Harness output | `<state>/runs/harness` | `<state>/runs/harness` |
| ACP session summaries | `<state>/workspaces/<scope>/acp` | `<state>/workspaces/<scope>/acp` |
| Workflow context packs | `<state>/workspaces/<scope>/context-packs` | `<state>/workspaces/<scope>/context-packs` |
| QMD package files | `<data>/qmd` | `<data>/qmd` |
| `lossless-claw` checkout | `<data>/plugins/lossless-claw` | `<data>/plugins/lossless-claw` |

Use `STRONGCLAW_DATA_DIR`, `STRONGCLAW_STATE_DIR`, `STRONGCLAW_LOG_DIR`,
`STRONGCLAW_RUNS_DIR`, or `STRONGCLAW_COMPOSE_STATE_DIR` when an operator needs
to override those defaults. The provided shell wrappers export
`STRONGCLAW_COMPOSE_STATE_DIR` automatically before invoking Docker Compose.

## Shared host contract

Regardless of host OS, the baseline flow is:

1. provision a dedicated non-admin runtime user with `./scripts/bootstrap/create_openclawsvc.sh`
2. clone the repo as that user
3. install the runtime package with `make install`
4. either prepare `platform/configs/varlock/.env.local` manually or let `make setup` / `clawops setup` create and normalize it interactively
5. prefer `make setup` for the baseline path after clone; it now guides Varlock env setup, managed secret backend selection, and OpenClaw model auth during setup
6. for the supported sparse+dense memory path, set `HYPERMEMORY_EMBEDDING_MODEL` and run `clawops setup --profile hypermemory`
7. run `./scripts/bootstrap/verify_hypermemory.sh` after hypermemory setup or rerenders
8. if Linux bootstrap just granted Docker access, open a fresh login shell and rerun the same `make setup` / `clawops setup` command; completed bootstrap work is auto-detected and skipped
9. contributors can additionally install `uv` and use `make dev && make test`; baseline companion-tool tests run through `uv run`, and bootstrap installs `uv` if the host does not already provide it
10. or run the lower-level steps explicitly with `./scripts/bootstrap/bootstrap.sh`, `./scripts/bootstrap/configure_varlock_env.sh`, `./scripts/bootstrap/render_openclaw_config.sh`, `./scripts/bootstrap/install_host_services.sh --activate`, and `./scripts/bootstrap/verify_baseline.sh`

## macOS host notes

- Preflight requires Homebrew.
- OrbStack, Rancher Desktop, Colima, and Docker Desktop are all acceptable as
  long as they expose `docker` plus `docker compose`.
- If one of those runtimes is installed but its Docker CLI integration is not
  enabled yet, bootstrap stops instead of installing Docker over it.
- Service definitions render into `~/Library/LaunchAgents`.
- Activate them with `launchctl bootstrap gui/$(id -u) ...`.
- The runtime-user and loopback-SSH flow is documented in
  `platform/docs/runbooks/macos-service-user-and-ssh.md`.

## Linux host notes

- The current Linux bootstrap path targets Debian/Ubuntu-style hosts with
  `apt-get`.
- Existing Docker-compatible runtimes are reused when they already expose
  `docker` plus `docker compose` for the runtime user.
- If no compatible runtime is detected, bootstrap installs Docker Engine as the
  fallback backend.
- Provision the runtime user with `./scripts/bootstrap/create_openclawsvc.sh`.
- Service definitions render into `~/.config/systemd/user`.
- Activate them with `systemctl --user daemon-reload` and
  `systemctl --user enable --now ...`.
- Use `loginctl enable-linger <user>` when user services must survive logout.
- Prefer rootless Docker or a tightly controlled `docker` group for the runtime
  user.
- The runtime-user and user-systemd flow is documented in
  `platform/docs/runbooks/linux-runtime-user-and-systemd.md`.

## Separate-host guidance

- Browser lab belongs on a separate host or hardened user session.
- ACP workers can run on the same operator host for evaluation, but a separate
  worker host is the safer steady-state.
- Langfuse and similar observability extras can live on a separate VM or sidecar
  host without changing the control-plane bootstrap contract.
