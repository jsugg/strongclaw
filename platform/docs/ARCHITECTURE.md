# Architecture

## Control plane

The main OpenClaw gateway is the control plane:
- channel ingress
- session routing
- memory
- control UI
- provider auth state

It stays on the trusted host, loopback-bound, token-authenticated, and private.

## Execution plane

Risky work is moved out of the control plane:
- sandboxed OpenClaw coding sessions
- ACP/acpx coding workers
- repository worktrees
- `clawops context codebase` indexing, hybrid retrieval, graph expansion, and context-pack assembly
- optional browser-lab runners

## Operations plane

Sidecars and platform helpers:
- LiteLLM + Postgres
- Qdrant for codebase and hypermemory vector retrieval
- Neo4j CE for codebase graph expansion
- OTel Collector
- backup / retention
- env/secret contract
- CI/CD gates

## Verification plane

Proves the above actually works:
- harness suites
- policy regression tests
- privacy scans
- charts and dashboards

## Why this split exists

The failure mode to avoid is hostile input directly driving privileged tools. The split ensures:
- reader lanes see hostile content but cannot act
- coder lanes can act but must operate in sandboxes or ACP workers
- reviewer lanes verify independently
- external writes go through policy + journal + allowlist checks

Codebase context follows that split:
- the OpenClaw `contextEngine` remains separate from StrongClaw codebase context
- `clawops context` is a generic namespace
- `codebase` is the first execution-plane context provider
