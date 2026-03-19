# Strongclaw / ClawOps

This repository is the **Strongclaw** bootstrap for a hardened,
production-oriented OpenClaw deployment and ships the **ClawOps** companion
tooling. It is intentionally broader than a simple install pack. It includes:

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
3. [`platform/docs/HOST_PLATFORMS.md`](platform/docs/HOST_PLATFORMS.md)
4. [`USAGE_GUIDE.md`](USAGE_GUIDE.md)
5. [`platform/docs/ARCHITECTURE.md`](platform/docs/ARCHITECTURE.md)
6. [`platform/docs/PRODUCTION_READINESS_CHECKLIST.md`](platform/docs/PRODUCTION_READINESS_CHECKLIST.md)

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
│   ├── systemd/                # Linux service templates
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
git clone <this repo> strongclaw
cd strongclaw

make install

make setup
```

`make setup` runs the guided `clawops setup` workflow inside the managed
environment. It bootstraps host prerequisites, creates or repairs
`platform/configs/varlock/.env.local`, prompts for missing setup input when
needed, configures OpenClaw model/provider auth, activates services, and runs
the baseline verification gate. The lower-level shell entrypoint remains
available at `./scripts/bootstrap/setup.sh` for manual or partial bring-up, and
you can call the CLI directly with `uv run --project . clawops setup`.

The bootstrap path reuses any existing Docker-compatible runtime that already
provides `docker` plus `docker compose`, and only installs Docker as a fallback
when no compatible runtime is detected.

Then continue with ACP workers, repo lexical context indexing, channels, and observability using the step order in [`SETUP_GUIDE.md`](SETUP_GUIDE.md).

If Linux bootstrap just added the runtime user to the `docker` group, start a
fresh login shell as that user and rerun:

```bash
make setup SETUP_ARGS="--skip-bootstrap"
```

For placeholder-backed variants, rerender through the profile-aware entrypoint
instead of merging raw overlays:

```bash
./scripts/bootstrap/render_openclaw_config.sh --profile acp
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local
./scripts/bootstrap/render_openclaw_config.sh --profile memory-pro-local-smart
```

The bootstrap verification flow keeps the `clawops verify-platform` entrypoints
on the operator path: sidecars can be probed directly, while the baseline gate
re-runs the sidecar, observability, and channel checks in static mode.

For a deeper post-setup scan, run:

```bash
make doctor
```

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
- ShellCheck for shell scripts
- mypy type checking
- Pyright type checking
- actionlint for GitHub Actions
- basic repository hygiene checks

Useful follow-up commands:

```bash
make help
make fmt
make lint
make imports
make typecheck
make actionlint
make shellcheck
make precommit
make dev-check
```

`make precommit` runs the mutating formatter/import/hygiene hooks first and then
verifies the full pre-commit stack in one final pass.

`make dev-check` builds on `make precommit` and then runs the test suite plus a
compile smoke. That keeps the two targets distinct: `make precommit` is the
repository normalization and hook gate, while `make dev-check` is the deeper
development verification pass.
