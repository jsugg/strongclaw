# Browser Lab

Browser automation is not part of the baseline platform.

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
clawops verify-platform browser-lab
```

Browser-lab stays optional and is excluded from baseline verification by default.
If your rollout enables browser-lab, either run the explicit browser-lab verifier
or include it in the baseline gate:

```bash
clawops baseline verify --include-browser-lab
```

## Included artifacts

- browser-lab compose stack
- Squid allowlist proxy config
- allowed domains list
- exfiltration smoke script
