# Browser Lab

Browser automation is optional and excluded from baseline checks unless explicitly requested.

## Requirements

- separate host or separate hardened OS user
- outbound allowlist proxy
- sacrificial accounts
- exfiltration smoke tests
- no production secrets on the browser runner

## Operating model

- bind browser-lab ports to loopback only
- reach the OpenClaw gateway through SSH tunneling, not direct LAN exposure
- never tunnel `9222` or `3128` to an operator workstation
- keep CDP pointed at `http://127.0.0.1:9222` from within the hardened session

Example gateway tunnel:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Verify the expected local-only bindings after startup:

```bash
docker compose -f platform/compose/docker-compose.browser-lab.yaml ps
```

Use first-class browser-lab verification commands when this surface is enabled:

```bash
clawops verify-platform browser-lab
clawops baseline verify --include-browser-lab
```

`clawops baseline verify` keeps browser-lab excluded by default. Add
`--include-browser-lab` only when rollout requires browser automation readiness
evidence.

## Included artifacts

- browser-lab compose stack
- Squid allowlist proxy config
- allowed domains list
- exfiltration smoke script
