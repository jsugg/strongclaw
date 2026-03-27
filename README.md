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
7. [`platform/docs/DEVFLOW.md`](platform/docs/DEVFLOW.md)

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
├── repo/                       # upstream/worktree operator contract
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

`make install` and the managed bootstrap path prefer Python `3.12` on supported
Darwin/Linux hosts as the compatibility baseline. That keeps the default
hypermemory local-rerank path installable on Intel macOS while remaining valid
across Apple Silicon Macs, Linux laptops, and supported 64-bit Raspberry Pi
setups. Python `3.13` remains supported for explicit use and CI coverage.

`make setup` runs the guided `clawops setup` workflow inside the managed
environment. It bootstraps host prerequisites, creates or repairs
`platform/configs/varlock/.env.local`, offers local or managed Varlock secret
backends for provider auth, prompts for missing setup input when needed,
configures OpenClaw model/provider auth, activates services, and runs the
baseline verification gate. The lower-level CLI entrypoint remains available
at `clawops setup` for manual or partial bring-up, and you can
call the CLI directly with `uv run --project . clawops setup`.

By default, StrongClaw now renders and provisions the
`hypermemory` stack. Set one embedding model name before you run
the no-arg setup path:

```bash
export HYPERMEMORY_EMBEDDING_MODEL=openai/text-embedding-3-small
uv run --project . clawops setup
clawops doctor
```

The shipped hypermemory configs now also enable planner-stage reranking with a
local `sentence-transformers` provider first and a `compatible-http` fallback.
Plain `uv sync` installs the local rerank dependency on the host/Python
combinations where upstream wheels are known to work: macOS arm64, macOS
x86_64 on Python 3.12, and Linux x86_64 or aarch64/arm64 on Python 3.12 or
3.13. For Raspberry Pi, that explicitly includes Raspberry Pi 4/5 running
64-bit Raspberry Pi OS or Ubuntu arm64. On unsupported combinations such as
macOS x86_64 with Python 3.13 or 32-bit Raspberry Pi Linux, StrongClaw skips
the local dependency and relies on `compatible-http` or fail-open behavior
instead of breaking installation.
On Intel macOS/Python 3.12 that compatibility path is pinned to
`sentence-transformers==3.4.1`, `torch==2.2.2`, and `numpy<2`. The shipped
config defaults `rerank.local.device` to `auto`, which selects `cuda` on
supported GPU hosts, `mps` on supported Apple Silicon hosts, and `cpu`
otherwise. If auto-selected acceleration fails at runtime, the local reranker
falls back to CPU automatically.

If you want the HTTP fallback available, set `HYPERMEMORY_RERANK_BASE_URL` and,
optionally, `HYPERMEMORY_RERANK_MODEL` / `HYPERMEMORY_RERANK_API_KEY`.

If you want the legacy OpenClaw built-ins instead, use the explicit
`openclaw-default` profile:

```bash
uv run --project . clawops config memory --set-profile openclaw-default
```

If you want the built-ins plus the experimental QMD backend, use
`openclaw-qmd`:

```bash
uv run --project . clawops config memory --set-profile openclaw-qmd
```

StrongClaw-generated runtime artifacts no longer default into the git checkout.
Harness output, ACP summaries, compose sidecar state, QMD package files, and
the managed `lossless-claw` checkout are written to OS-appropriate app
data/state directories instead.

The bootstrap path reuses any existing Docker-compatible runtime that already
provides `docker` plus `docker compose`, and only installs Docker as a fallback
when no compatible runtime is detected.

Then continue with ACP workers, repo lexical context indexing, channels, and observability using the step order in [`SETUP_GUIDE.md`](SETUP_GUIDE.md).

If Linux bootstrap just added the runtime user to the `docker` group, setup now
pauses with a clear message. Open a fresh login shell as that user and rerun
the same `make setup` or `clawops setup` command; completed bootstrap work is
detected automatically.

For placeholder-backed variants, rerender through the profile-aware entrypoint
instead of merging raw overlays:

```bash
clawops render-openclaw-config --repo-root . --profile acp
clawops render-openclaw-config --repo-root . --profile hypermemory
clawops render-openclaw-config --repo-root . --profile memory-lancedb-pro
```

The StrongClaw-managed `memory-lancedb-pro` profile keeps smart extraction on,
but still ships with `autoRecall = false`, `sessionStrategy = "none"`,
`selfImprovement.enabled = false`, and `enableManagementTools = false`.

The bootstrap verification flow keeps the `clawops verify-platform` entrypoints
on the operator path: sidecars can be probed directly, while the baseline gate
re-runs the sidecar, observability, and channel checks in static mode.

For a deeper post-setup scan, run:

```bash
make doctor
```

Supported toolchain versions are documented in
[`platform/docs/HOST_PLATFORMS.md`](platform/docs/HOST_PLATFORMS.md). The
current validated baseline is Python `3.12` or `3.13`, Node `22.16.x` or
`24.x`, `uv 0.10.9`, Varlock `0.5.0`, OpenClaw `2026.3.13`, ACPX `0.3.0`,
QMD `2.0.1`, and `lossless-claw v0.3.0`.

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

If you want shorter commands in a shell session, sync the dev environment once
and activate it before running tools directly:

```bash
uv sync --locked
source .venv/bin/activate
pytest -q
clawops --help
deactivate
```

`uv run ...` remains the default documented path for one-off commands from the
repo root. Activating `.venv/` is optional and is mainly useful when you want
plain `pytest`, `clawops`, and other project commands without repeating
`uv run`.

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

`make shellcheck` and `make precommit` expect a local `shellcheck` binary on
`PATH` now that the repo hook uses the system binary instead of a Docker-backed
wrapper. `brew install shellcheck` and `sudo apt-get install shellcheck` both
work.

## Devflow

The repository now exposes a production devflow surface for multi-stage
planning, execution, recovery, and audit:

```bash
clawops devflow plan --repo-root . --goal "Fix regression and add coverage"
clawops devflow run --repo-root . --goal "Fix regression and add coverage" --approved-by operator
clawops devflow status --repo-root . --run-id <run-id>
clawops devflow resume --repo-root . --run-id <run-id> --approved-by operator
clawops devflow audit --repo-root . --run-id <run-id>
```
