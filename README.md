# OpenClaw Platform Bootstrap

This repository is a **platform bootstrap** for a hardened, production-oriented OpenClaw deployment. It is intentionally broader than a simple install pack. It includes:

- a hardened OpenClaw control plane baseline
- a separate execution plane for ACP/acpx coding workers
- a policy engine with operation journaling and idempotency
- a repository context/indexing service
- sidecars for LiteLLM, Postgres, and OpenTelemetry Collector
- optional Langfuse and browser-lab scaffolding
- channel rollout runbooks and allowlist automation
- backup, restore, retention, and incident response tooling
- CI/CD security gates and a harness for routing / policy / privacy regressions

## Entry points

Read these in order:

1. [`QUICKSTART.md`](QUICKSTART.md)
2. [`SETUP_GUIDE.md`](SETUP_GUIDE.md)
3. [`USAGE_GUIDE.md`](USAGE_GUIDE.md)
4. [`platform/docs/ARCHITECTURE.md`](platform/docs/ARCHITECTURE.md)
5. [`platform/docs/MEMORY_V2.md`](platform/docs/MEMORY_V2.md)

The opt-in durable memory rollout is documented in
[`platform/docs/MEMORY_V2.md`](platform/docs/MEMORY_V2.md).

## Repository map

```text
.
├── .github/                     # CI/CD gates
├── platform/
│   ├── compose/                # sidecar and browser-lab compose stacks
│   ├── configs/                # OpenClaw, LiteLLM, OTel, Varlock, policy, context, workflows
│   ├── docs/                   # architecture, runbooks, and production checklists
│   ├── launchd/                # macOS service templates
│   ├── skills/                 # local/reviewed/quarantine skill layout
│   ├── systemd/                # Linux migration service units
│   ├── workers/                # acpx, QMD, and browser-lab artifacts
│   └── workspace/              # per-role AGENTS/MEMORY bootstrap
├── scripts/
│   ├── bootstrap/              # bring-up scripts
│   ├── ci/                     # CI helpers
│   ├── ops/                    # runtime helpers
│   ├── recovery/               # backup/restore/rotation helpers
│   └── workers/                # acpx and browser-lab helpers
├── security/                   # CodeQL, Semgrep, Gitleaks, Trivy config
├── src/clawops/                # companion Python tooling
└── tests/                      # unit tests for companion tooling
```

## Platform posture

This pack assumes:

- **one trusted operator boundary per OpenClaw gateway**
- OpenClaw stays **loopback-bound** and **token-authenticated**
- risky file/runtime work runs in **sandboxed OpenClaw sessions** and/or **ACP workers**
- external side effects go through **wrapper services** with **policy checks** and **operation journaling**
- secrets are managed as **Varlock outer env contract** + **OpenClaw inner SecretRef runtime binding**
- browser automation is **off by default** and isolated into a **browser lab**

## Fastest path

```bash
git clone <this repo> openclaw-platform-bootstrap
cd openclaw-platform-bootstrap

python3 -m pip install -e .
make test

./scripts/bootstrap/bootstrap_macos.sh
./scripts/bootstrap/render_openclaw_config.sh
./scripts/bootstrap/bootstrap_sidecars.sh
./scripts/bootstrap/verify_sidecars.sh
./scripts/bootstrap/verify_baseline.sh
```

Then continue with ACP workers, repo lexical context indexing, channels, and observability using the step order in [`SETUP_GUIDE.md`](SETUP_GUIDE.md).

The bootstrap verification flow keeps the `clawops verify-platform` entrypoints
on the operator path: sidecars can be probed directly, while the baseline gate
re-runs the sidecar, observability, and channel checks in static mode.

## Development

For local development, use `uv` for the project environment and install the
pre-commit hooks once:

```bash
make dev
```

That syncs the `dev` extra into `.venv/` and installs hooks for:

- isort import sorting
- Black formatting
- Ruff linting
- mypy type checking
- Pyright type checking
- actionlint for GitHub Actions
- basic repository hygiene checks

Useful follow-up commands:

```bash
make fmt
make lint
make imports
make typecheck
make actionlint
make precommit
make dev-check
```

`make precommit` runs the full pre-commit hook stack across the repository and
reruns automatically after formatter/import/hygiene fixes so auto-fixable
issues are applied before verification.

`make dev-check` builds on `make precommit` and then runs the test suite plus a
compile smoke. That keeps the two targets distinct: `make precommit` is the
repository normalization and hook gate, while `make dev-check` is the deeper
development verification pass.
