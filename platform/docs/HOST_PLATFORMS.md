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

## Shared host contract

Regardless of host OS, the baseline flow is:

1. provision a dedicated non-admin runtime user with `./scripts/bootstrap/create_openclawsvc.sh`
2. clone the repo as that user
3. install the runtime package with `make install`
4. either prepare `platform/configs/varlock/.env.local` manually or let `make setup` / `clawops setup` create and normalize it interactively
5. prefer `make setup` for the baseline path after clone; it now guides Varlock env setup and OpenClaw model auth during setup
6. if Linux bootstrap just granted Docker access, start a fresh login shell and rerun `make setup SETUP_ARGS="--skip-bootstrap"` or `clawops setup --skip-bootstrap`
7. contributors can additionally install `uv` and use `make dev && make test`; baseline companion-tool tests run through `uv run`, and bootstrap installs `uv` if the host does not already provide it
8. or run the lower-level steps explicitly with `./scripts/bootstrap/bootstrap.sh`, `./scripts/bootstrap/configure_varlock_env.sh`, `./scripts/bootstrap/render_openclaw_config.sh`, `./scripts/bootstrap/install_host_services.sh --activate`, and `./scripts/bootstrap/verify_baseline.sh`

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
