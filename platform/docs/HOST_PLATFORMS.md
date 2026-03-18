# Host Platforms

Strongclaw supports two operator-host platforms:

- macOS hosts using Homebrew plus `launchd`
- Linux hosts using `apt-get` plus user-level `systemd`

Both use the same bootstrap entrypoints, config overlays, and verification
gates.

## Shared host contract

Regardless of host OS, the baseline flow is:

1. provision a dedicated non-admin runtime user with `./scripts/bootstrap/create_openclawsvc.sh`
2. clone the repo as that user
3. install the companion tooling with `make dev && make test`
4. bootstrap the host with `./scripts/bootstrap/bootstrap_host.sh`
5. render the OpenClaw config with `./scripts/bootstrap/render_openclaw_config.sh`
6. render host service definitions with `./scripts/bootstrap/install_host_services.sh`
7. activate the rendered services for the host-native service manager
8. verify the baseline with `./scripts/bootstrap/verify_baseline.sh`

## macOS host notes

- Preflight requires Homebrew.
- Service definitions render into `~/Library/LaunchAgents`.
- Activate them with `launchctl bootstrap gui/$(id -u) ...`.
- The runtime-user and loopback-SSH flow is documented in
  `platform/docs/runbooks/macos-service-user-and-ssh.md`.

## Linux host notes

- The current Linux bootstrap path targets Debian/Ubuntu-style hosts with
  `apt-get`.
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
