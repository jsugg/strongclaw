# Host Platforms

Strongclaw supports two operator-host platforms:

- macOS hosts using Homebrew plus `launchd`
- Linux hosts using `apt-get` plus user-level `systemd`

Both use the same bootstrap entrypoints, config overlays, and verification gates.

The bootstrap contract is runtime-aware: if the host already has a Docker-compatible runtime that exposes `docker` plus `docker compose`, Strongclaw reuses it. Docker is installed only as the fallback runtime when no compatible backend is detected.

## Compatibility matrix

StrongClaw's supported baseline is derived from the codebase constraints plus the pinned external tools that setup installs.

| Component | Supported / pinned version | Why |
| --- | --- | --- |
| Python | `3.12`, `3.13` | `pyproject.toml` requires `>=3.12`, and Ruff, Black, mypy, and Pyright all target Python 3.12 syntax/features. |
| Managed install baseline | Python `3.12` | `make install` and bootstrap prefer Python `3.12` on supported Darwin/Linux hosts so the default hypermemory local-rerank path stays on the broadest compatible interpreter. |
| Node.js | `22.16.x`, `24.x` | OpenClaw `2026.3.13` requires `>=22.16.0`; ACPX `0.3.0`, Varlock `0.5.0`, and QMD `2.0.1` all require Node 22+. |
| `uv` | `0.10.9` | Setup and CI pin this version for reproducible environment sync. |
| Varlock | `0.5.0` | Setup installs and verifies this exact version. |
| OpenClaw | `2026.3.13` | Setup installs this exact CLI version. |
| ACPX | `0.3.0` | Setup installs this exact CLI version. |
| QMD | `2.0.1` | Setup installs this exact package version behind the `~/.bun/bin/qmd` wrapper. |
| `lossless-claw` | `v0.3.0` | Setup installs this exact git ref for the context-engine plugin. |
| Hypermemory local rerank | Continuously validated on Linux `x86_64` with Python `3.12`/`3.13`; compatibility pins also exist for macOS `arm64`, macOS `x86_64`, and Linux `aarch64`/`arm64`, but those surfaces remain operator-validated and best-effort until they land in CI | Upstream wheel coverage is narrower than the base project matrix. Unsupported combinations skip the local `sentence-transformers` dependency and fall back to `compatible-http` or fail-open search order. The shipped rerank config defaults `rerank.local.device: auto`, which prefers `cuda`, then `mps`, then `cpu`, and retries on CPU if auto-selected acceleration fails. |

CI enforces this support statement through:

- a Python matrix on `3.12` and `3.13`
- a setup/install smoke matrix on Node `22.16.0` and `24.13.1`
- a vendored memory-plugin integration matrix on Node `22.16.0` and `24.13.1`
- a `strongclaw-hypermemory` OpenClaw host-functional lane on Ubuntu

For low-end or older hosts, this split matters:

- x86_64 Linux hosts stay on the continuously validated local rerank path
- Apple Silicon Macs, Intel Macs, and Linux arm64 hosts can use the documented compatibility pins, but those combinations are best-effort until they are added to CI
- Raspberry Pi 4/5 running 64-bit Raspberry Pi OS or Ubuntu arm64 should be treated as operator-validated, best-effort local rerank surfaces
- 32-bit Raspberry Pi Linux hosts skip the local rerank dependency and should use `compatible-http` if reranking is required

## Runtime data locations

StrongClaw-generated runtime artifacts should not live inside the repository checkout. The setup, doctor, harness, ACP runner, workflow context-pack, and compose helper commands now default to OS-appropriate app directories instead.

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

Use `STRONGCLAW_DATA_DIR`, `STRONGCLAW_STATE_DIR`, `STRONGCLAW_LOG_DIR`, `STRONGCLAW_RUNS_DIR`, or `STRONGCLAW_COMPOSE_STATE_DIR` when an operator needs to override those defaults. The Python-owned compose commands export `STRONGCLAW_COMPOSE_STATE_DIR` automatically before invoking Docker Compose.

## Shared host contract

Regardless of host OS, the baseline flow is:

1. provision a dedicated non-admin runtime user with `your platform-native runtime-user provisioning flow`
2. clone the repo as that user
3. install the runtime package with `make install`
4. either prepare the managed Varlock env manually or let `make setup` / `clawops setup` create and normalize it interactively
5. prefer `make setup` for the baseline path after clone; it now guides Varlock env setup, managed secret backend selection, and OpenClaw model auth during setup
6. for the supported sparse+dense memory path, set `HYPERMEMORY_EMBEDDING_MODEL` and run `clawops setup --profile hypermemory`
7. run `clawops hypermemory --config ~/.config/strongclaw/memory/hypermemory.yaml verify` after hypermemory setup or rerenders
8. if Linux bootstrap just granted Docker access, open a fresh login shell and rerun the same `make setup` / `clawops setup` command; completed bootstrap work is auto-detected and skipped
9. contributors can additionally install `uv` and use `make dev && make test`; for shorter interactive-shell commands, `uv sync --locked && source .venv/bin/activate` enables plain `pytest -q` and `clawops ...`; baseline companion-tool tests run through `uv run`, and bootstrap installs `uv` if the host does not already provide it
10. or run the lower-level steps explicitly with `clawops bootstrap`, `clawops varlock-env configure`, `clawops render-openclaw-config`, `clawops services install --activate`, and `clawops baseline verify`

## macOS host notes

- Preflight requires Homebrew.
- OrbStack, Rancher Desktop, Colima, and Docker Desktop are all acceptable as
long as they expose `docker` plus `docker compose`.
- `clawops ops sidecars up` now phases hosted macOS sidecar startup: Postgres
comes up first, StrongClaw runs LiteLLM Prisma bootstrap as a transient compose run, and only then does the long-lived LiteLLM proxy start. That keeps cold-database bootstrap work out of the runtime health window without encoding init containers into the steady-state compose topology.
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
- Provision the runtime user with `your platform-native runtime-user provisioning flow`.
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
