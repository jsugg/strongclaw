# Production Readiness Checklist

- [ ] dedicated OS user
- [ ] loopback-bound gateway
- [ ] token auth enabled
- [ ] `session.dmScope = per-channel-peer`
- [ ] sandbox mode `all`
- [ ] elevated exec disabled
- [ ] plugins/skills default-deny
- [ ] sidecars healthy
- [ ] `openclaw doctor` clean
- [ ] `openclaw security audit --deep` clean
- [ ] `openclaw secrets audit --check` clean
- [ ] operation journal initialized
- [ ] policy regression suite green
- [ ] backup and restore tested
- [ ] channel allowlists durable
- [ ] browser lab isolated or disabled
- [ ] if browser-lab is enabled, run `clawops verify-platform browser-lab` and confirm loopback-only bindings
- [ ] remote operator access uses SSH tunnel to gateway only

`clawops repo doctor` remains an operator/development contract for `repo/upstream`
and managed worktree flows. It is not part of the production baseline or launch
readiness bar.
