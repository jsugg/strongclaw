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
- [ ] browser-lab ports verified loopback-only
- [ ] remote operator access uses SSH tunnel to gateway only
