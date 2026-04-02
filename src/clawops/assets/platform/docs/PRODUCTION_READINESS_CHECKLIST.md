# Production Readiness Checklist

Use this checklist as the operator-facing go/no-go gate after setup succeeds and
before you treat a host as launch-ready.

## 1. Host and gateway boundary

- [ ] dedicated OS user owns the StrongClaw/OpenClaw runtime
- [ ] gateway stays loopback-bound and is only reached through an SSH tunnel
- [ ] token auth is enabled
- [ ] `session.dmScope = per-channel-peer`
- [ ] sandbox mode is `all`
- [ ] elevated exec is disabled
- [ ] plugins and skills stay default-deny until reviewed

## 2. Release-ready verification commands

Run these command-backed checks and keep the resulting evidence with the host
handoff or launch packet:

- [ ] `clawops doctor` is clean
- [ ] `clawops baseline verify` is clean
- [ ] `clawops verify-platform sidecars` is clean
- [ ] `clawops verify-platform observability` is clean
- [ ] `clawops verify-platform channels` is clean when rollout includes operator channels
- [ ] `openclaw doctor` is clean
- [ ] `openclaw security audit --deep` is clean
- [ ] `openclaw secrets audit --check` is clean

`clawops doctor-host` and `clawops doctor --skip-runtime --no-model-probe` are
useful host-only or degraded diagnostics, but they are **not** launch-ready or
release-ready substitutes for `clawops doctor`.

## 3. Recovery and durability

- [ ] operation journal is initialized and writable
- [ ] `clawops recovery backup-create` succeeds against the production home
- [ ] `clawops recovery backup-verify <archive>` succeeds for a freshly created archive
- [ ] `clawops recovery restore <archive> <clean-home>` has been tested on a clean destination
- [ ] backup retention is configured and reviewed
- [ ] channel allowlists are durable and versioned

## 4. Optional-but-exposed launch surfaces

- [ ] browser-lab is either disabled or isolated to a separate host or hardened OS user
- [ ] if browser-lab is enabled, `clawops verify-platform browser-lab` is clean and browser-lab ports remain loopback-only
- [ ] if browser-lab is intentionally out of scope for this rollout, `clawops baseline verify --exclude-browser-lab` is the documented gate result for the launch packet
- [ ] remote operator access uses an SSH tunnel to the gateway only; never tunnel browser-lab ports such as `9222` or `3128`

`clawops repo doctor` remains an operator/development contract for `repo/upstream`
and managed worktree flows. It is not part of the production baseline or launch
readiness bar.
