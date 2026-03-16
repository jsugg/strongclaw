# Browser lab

This directory contains the isolated browser automation scaffolding.

The browser lab must run on:
- a separate host, or
- a separate OS user plus isolated compose stack, preferably on Linux

Operator access should tunnel only the gateway port:

```bash
ssh -N -L 18789:127.0.0.1:18789 <gateway-user>@<gateway-host>
```

Do not tunnel `9222` or `3128` to an operator workstation. Keep the browser lab
reachable only from the hardened session that runs OpenClaw.

Included:
- `squid.conf` outbound allowlist proxy
- `allowed-domains.txt`
- exfiltration smoke tests
