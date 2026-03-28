# Topologies

## Laptop baseline

```text
[Control UI]
     |
[OpenClaw Gateway] --loopback--> [LiteLLM]
     |                           [Postgres]
     |                           [OTel Collector]
     +--> sandboxed sessions
```

## VPS / home server

```text
[SSH/Tailscale]
     |
[Gateway Host]
   |-- OpenClaw
   |-- LiteLLM
   |-- OTel Collector
   |-- Postgres
   `-- no public browser lab
```

## Full split with browser lab

```text
[Gateway Host] -----> [ACP Worker Host]
     |
     `-----> [Browser Lab Host]
```

The browser lab host should not hold control-plane secrets.
