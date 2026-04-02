# CI and Security

The repository includes:

- CodeQL
- Semgrep
- Gitleaks
- Trivy
- Policy Harness Smoke Tests
- Nightly Test Run
- Repository Dependency Snapshot from a generated SPDX SBOM snapshot
- Memory Plugin Integration Checks for the vendored `memory-lancedb-pro` bundle (`npm test` plus `openclaw@2026.3.13` host-functional coverage)
- `strongclaw-hypermemory` host-functional checks through the local plugin SDK stub
- Devflow contract checks for the public `clawops devflow` surface
- tagged release builds with artifact verification, GitHub Release assets, build provenance, and SBOM attestations
- Upstream Integration Validation

Workflow policy:

- GitHub Actions workflows stay thin. Multi-step operational logic lives in
semantic helper entrypoints under `tests/scripts/`, with unit coverage in `tests/suites/unit/ci/` and repo contract coverage under `tests/suites/contracts/repo/`.

## Pull-request gate orchestration

Pull requests now flow through `.github/workflows/ci-gate.yml`, which is the
single required branch-protection check for `main` via the stable
`CI / Verdict` context.

- The gate always runs on `pull_request` and classifies file changes with
`dorny/paths-filter` using `.github/ci/ci-gate-filters.yml`.
- Docs-only pull requests run only the lightweight docs parity lane:
`uv run pytest -q tests/suites/contracts/repo/test_docs_parity.py`.
- Heavy CI lanes are orchestrated as reusable workflow calls from the gate:
`harness.yml`, `compatibility-matrix.yml`, `memory-plugin-verification.yml`,
`fresh-host-acceptance.yml`, and `security.yml`.
- Stage ordering keeps fast signals first (`harness`, `compatibility_matrix`,
`memory_plugin`) and gates long lanes (`fresh_host`, `security`) on stage-one
success.
- The final `Verdict` job always runs, summarizes lane outcomes, and fails when
any required lane does not complete successfully.

## Fresh-host acceptance

`.github/workflows/fresh-host-acceptance.yml` exercises the real bootstrap, setup, service activation, and repo-local sidecar/browser-lab flows on hosted Linux and macOS runners. It delegates the reusable execution lane to `.github/workflows/fresh-host-core.yml`.

- Each run writes a GitHub job summary with the runner label, runtime provider,
cache toggles, phase timings, and the effective hosted macOS Colima sizing.
- Each run now renders an explicit context preview immediately after
`prepare-context`, so operators can inspect planned phases, compose targets,
runtime settings, and scenario paths before runtime install or scenario
execution begins.
- Each run uploads a `fresh-host-reports` artifact subtree with runtime
diagnostics (`docker info`, image inventory, launchd state, and runtime status output), context preview JSON, and rendered host artifacts.
- Hosted macOS acceptance is pinned to `macos-15-intel`. GitHub's standard
`macos-15` arm64 runners are available on public repositories, but GitHub documents that nested virtualization is not supported on arm64 macOS hosted runners, so Colima/OrbStack cannot provide a Docker backend there.
- The hosted macOS job installs Lima and Colima directly, then sizes Colima for
the runner instead of using the old fixed `2 CPU / 4 GiB` VM.
- Hosted macOS acceptance uses the `ci-hosted-macos` compose variant so
sidecars and browser-lab mutable data live in Docker-managed volumes instead of FUSE-backed host bind mounts. That avoids the hosted-Colima filesystem regressions seen with Qdrant and Postgres while preserving the real `clawops` setup, launchd activation, and repo-local stack flows.
- `workflow_dispatch` can benchmark cache toggles for the supported hosted
macOS path without changing the required PR gate.
- The workflow stays declarative by delegating runtime setup, image warming,
diagnostics, and summary generation to executable helper scripts under `tests/scripts/`. Hosted macOS image warming restores a cached Docker image archive when available, then verifies compose image availability with bounded retries and heartbeat logging as a fallback.
- `.github/workflows/nightly.yml` warms the fresh-host caches before it calls the reusable fresh-host core lane for the scheduled validation sweep.
- Repository workflow contract tests verify that shell steps invoking
`tests/scripts/*.py` either call an explicit Python interpreter or target an executable script, so nightly cache warming cannot silently regress on file mode drift.

## Vendored plugin verification

The vendored `platform/plugins/memory-lancedb-pro` bundle is verified on GitHub Actions in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `the vendored-memory plugin verification workflow`.
- That flow reuses `clawops config memory --set-profile memory-lancedb-pro`, which
auto-detects the host and installs the default LanceDB dependency on supported hosts or the Intel-macOS fallback `@lancedb/lancedb@0.22.3`.
- The workflow delegates the host-functional orchestration to
`tests/scripts/memory_plugin_verification.py`, which installs the pinned `openclaw@2026.3.13` CLI into a temporary tool directory and runs the host-functional `npm run test:openclaw-host` suite.
- The host-functional step clears ambient AWS credential env vars first so
local Bedrock model discovery noise does not contaminate test assertions.

## strongclaw-hypermemory host verification

The repo-local `platform/plugins/strongclaw-hypermemory` bundle is also verified in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `the strongclaw-hypermemory verification workflow`.
- That flow runs `npm run test:openclaw-host` inside the plugin bundle.
- The host-functional test creates a temporary sqlite-backed `hypermemory`
config, registers the plugin through the local SDK stub, verifies the exported `memory` CLI surface and subcommands, and exercises the strongclaw-owned `memory_search` and `memory_get` tool paths.

## Policy for new code

- no direct secrets in config
- new skills/plugins require scan + review
- harness cases should be added for new security-sensitive behavior
- browser-lab changes need explicit review

## Dependency and release provenance

- `.github/workflows/dependency-submission.yml` generates `sbom.spdx.json` with
`anchore/sbom-action` and submits the resulting dependency snapshot to the GitHub dependency graph.
- `.github/workflows/security.yml`,
`.github/workflows/upstream-merge-validation.yml`, and `.github/workflows/release.yml` all call the centralized `clawops supply-chain quality-gate` surface so linting, typing, tests, coverage, and compile checks stay aligned.
- That shared quality gate now enforces one overall coverage floor plus named
minimums for critical operational modules before downstream publish or release
steps can continue.
- The compatibility matrix, memory-plugin verification, security, and release
workflows delegate their nontrivial operational steps to `tests/scripts/` helper CLIs instead of embedding shell blobs or Python heredocs directly in YAML.
- Those Ubuntu quality-gate workflows install the distro `shellcheck` binary
before invoking the shared gate, and the repo's `pre-commit` hook now uses that system binary instead of a Docker-backed hook.
- `.github/workflows/security.yml` enforces independent review for pull requests touching security-critical paths (auth, secrets, CI/infrastructure, dependency manifests, and browser-lab surfaces) via `tests/scripts/security_workflow.py enforce-independent-review`.
- `.github/workflows/security.yml` installs a pinned `semgrep` CLI directly
instead of relying on the Docker-backed Semgrep action, which keeps the lane off Docker Hub.
- The Semgrep ruleset covers the repo's Python-heavy risk surfaces, including
raw tar extraction, traversal-prone archive-member joins, `subprocess`
`shell=True`, and unsafe deserialization helpers.
- `.github/workflows/security.yml` verifies the pinned `gitleaks` and `syft`
tarball SHA-256 digests before extracting the binaries through the dedicated helper script.
- That same security lane now executes two operational smoke checks through
`tests/scripts/security_workflow.py`: channel rollout contract parity
(`verify-channels-contract`) plus a disposable backup/verify/restore cycle
(`run-recovery-smoke`) so launch-critical channel and recovery paths produce
executable CI evidence.
- `.github/workflows/fresh-host-core.yml` now includes explicit fresh-host phases for channel acceptance (`exercise-channels-runtime`) and recovery smoke (`exercise-recovery-smoke`) in the Linux and macOS sidecar scenarios, so release prerequisites also carry those evidentiary checks.
- `.github/workflows/release.yml` now blocks publication on three repo-controlled
prerequisites: the centralized release quality gate, the reusable fresh-host
acceptance workflow, and the reusable memory-plugin verification workflow. It
builds the Python sdist/wheel only after those prerequisites pass, verifies each
artifact with `twine check` plus fresh install smoke tests through the
dedicated release helper script, publishes or updates the GitHub release with
`gh`, and emits GitHub attestations for both build provenance and the generated
SBOM.
- `.github/workflows/upstream-merge-validation.yml` runs the repo quality gate
plus nightly validation steps after an upstream merge lands in the fork.
- `.github/workflows/memory-plugin-verification.yml` runs the dedicated
hypermemory Qdrant checks against the official pinned Qdrant GHCR image instead of Docker Hub.
- `.github/workflows/devflow-contract.yml` syncs the locked environment,
compile-checks the repo, runs targeted devflow tests, and validates `clawops devflow plan --goal "contract smoke"` without live ACP providers.
- Operators can verify published provenance with GitHub's attestation tooling
after a tagged release lands.

Canonical plugin support status lives in [Plugin Inventory](./PLUGIN_INVENTORY.md).
