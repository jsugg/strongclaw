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
- a manual end-to-end acceptance pipeline that chains the deep quality gate, policy harness, plugin verification, security scans, and live runtime, stack, and model smokes

Workflow policy:

- GitHub Actions workflows stay thin. Multi-step operational logic lives in
semantic helper entrypoints under `tests/scripts/`, with unit coverage in `tests/suites/unit/ci/` and repo contract coverage under `tests/suites/contracts/repo/`.
- Local contributors can run `make ci` as the non-mutating source CI mirror. It
  wraps `clawops supply-chain quality-gate` and may write local reports, but it
  does not run formatting hooks that edit source files.

## CodeQL baseline governance

`.github/codeql-triage.json` records the captured 15-alert baseline and an
explicit accepted-risk rationale for every alert. All 15 findings are inside
vendored third-party `memory-lancedb-pro`; this repository does not maintain or
patch that plugin. Strongclaw maintains only `strongclaw-hypermemory`. Live
alerts are dismissed as `won't fix` with this maintenance-boundary rationale,
not misclassified as false positives.
`security/codeql/codeql-config.yml` excludes that vendored plugin from future
analysis while continuing to scan maintained `strongclaw-hypermemory`.

`.github/workflows/codeql-alert-age.yml` is scheduled/manual and report-only.
It inventories open high/critical alerts at least 30 days old, uploads JSON and
Markdown evidence for 14 days, and never runs on pull requests or becomes a
required status. Any future age-based enforcement requires a clean baseline and
separate owner approval.

Trusted post-merge verification:

```bash
gh api 'repos/jsugg/strongclaw/code-scanning/alerts?per_page=100&state=open'
```

## Pull-request gate orchestration

Pull requests now flow through `.github/workflows/ci-gate.yml`, which is the
single required branch-protection check for `main` via the stable
`Verdict` API context. GitHub's UI may display that workflow/job as
`CI / Verdict`; branch protection and release preflight audits must still use
the exact API context `Verdict`.

- `.github/required-checks.json` is the repo-owned manifest for the required
  check, solo-maintainer branch-protection settings, and rollback evidence.
- The gate always runs on `pull_request` and on pushes to `main`, then
  classifies file changes with
`dorny/paths-filter` using `.github/ci/ci-gate-filters.yml`.
- Docs-only pull requests run only the lightweight docs parity lane:
`uv run pytest -q tests/suites/contracts/repo/test_docs_parity.py`.
- Heavy CI lanes are orchestrated as reusable workflow calls from the gate:
`harness.yml`, `compatibility-matrix.yml`, `memory-plugin-verification.yml`,
`fresh-host-acceptance.yml`, and `security.yml`.
- Dependency manifest and lockfile pull requests also run the blocking
  `Dependency Review` lane. It runs GitHub's dependency-review action and audits
  the resolved `uv.lock` export so vulnerable or unresolvable dependency state
  fails before merge.
- The `uv` Dependabot lane uses `versioning-strategy: increase` so exact
  compatibility pins move to the selected version instead of producing
  overlapping old/new requirements during lock regeneration.
- Stage ordering keeps fast signals first (`harness`, `compatibility_matrix`,
`memory_plugin`) and gates long lanes (`fresh_host`, `security`) on stage-one
success.
- The final `Verdict` job always runs, summarizes lane outcomes, uploads a
  compact `ci-verdict` artifact containing `ci-verdict.json`, and fails when any
  required lane does not complete successfully.
- Release tag preflight requires a tagged commit that is reachable from
  `origin/main` and has a successful `Verdict` check run, so `main` pushes also
  receive the same stable verdict context.

## Repository governance controls

- `CODEOWNERS` currently assigns all paths to `@jsugg`, the sole maintainer.
- `main` requires a pull request but no approving review while there is only one
  maintainer; GitHub cannot accept the author's self-review. `CODEOWNERS` still
  routes ownership and voluntary review requests.
- Merge authority is controlled through repository access. `jsugg` is the only
  collaborator, so external contributors may open pull requests from forks but
  cannot merge them. On this personal-account repository, granting collaborator
  access also grants merge capability.
- Admin enforcement stays disabled only to preserve the documented emergency
  break-glass path. Routine merges must satisfy the pull-request, strict
  `Verdict`, and conversation-resolution gates.
- Branch protection keeps `Verdict` strict/up-to-date, disables force pushes and
  deletions, and requires conversation resolution.
- `tests/scripts/required_checks_policy.py audit-live` is a trusted maintainer
  command only. Do not run it on untrusted fork pull requests because it reads
  live repository policy through GitHub CLI credentials.

Snapshot audit example:

```bash
uv run python tests/scripts/required_checks_policy.py audit-snapshot \
  --branch-protection-json "$(cat .local/live-config-snapshots/latest-after)/branch-protection.json"
```

Live audit example:

```bash
uv run python tests/scripts/required_checks_policy.py audit-live \
  --repository jsugg/strongclaw \
  --branch main
```

## End-to-end acceptance

`.github/workflows/e2e-acceptance.yml` is a manual (`workflow_dispatch`) acceptance pipeline for release candidates and large changes. It chains the heavy reusable lanes and adds live runtime smokes that the per-PR gate does not run.

- The `scope` input selects depth. `standard` runs the deep quality gate (`clawops supply-chain quality-gate`), the policy harness, memory-plugin verification, and the security scans on Linux. `full` additionally runs the compatibility matrix and the reusable fresh-host install acceptance, which adds hosted macOS and multi-arch coverage and is slow.
- `Runtime Smokes` exercise public CLI surfaces through `tests/scripts/e2e_acceptance.py`: skills intake (`skills-smoke`), worktree lifecycle (`worktree-smoke`), and an ACP worker session (`acp-smoke`).
- `Live Stack Smokes` bring up the Postgres, Qdrant, Neo4j, and OTel collector sidecars from `platform/compose/docker-compose.aux-stack.yaml`, then run observability, retrieval, and degradation failure-injection smokes against the live stack.
- `Live Model Smokes` start a pinned local Ollama runtime, pull a tiny model, and run a live inference plus agent-turn smoke.
- The `Acceptance Verdict` job always runs, prints each lane result, and fails if any in-scope lane failed or was cancelled while treating out-of-scope skips as passing.

## Fresh-host acceptance

`.github/workflows/fresh-host-acceptance.yml` exercises the real bootstrap, setup, service activation, and repo-local sidecar/browser-lab flows on hosted Linux and macOS runners. It delegates the reusable execution lane to `.github/workflows/fresh-host-core.yml`.

- Each run writes a GitHub job summary with the runner label, runtime provider,
cache toggles, and phase timings.
- Each run now renders an explicit context preview immediately after
`prepare-context`, so operators can inspect planned phases, compose targets,
runtime settings, and scenario paths before runtime install or scenario
execution begins.
- Each run uploads a `fresh-host-reports` artifact subtree with runtime
diagnostics (`docker info`, image inventory, launchd state, and runtime status output), context preview JSON, and rendered host artifacts.
- Hosted macOS acceptance is pinned to `macos-15-intel`. GitHub's standard
`macos-15` arm64 runners do not expose hardware virtualization (neither HVF nor
Apple Virtualization.framework), so no Docker runtime can provide a backend there.
- The hosted macOS job installs OrbStack v1.5.1 via a cached DMG
(`orbstack-v1.5.1-16857-amd64`) and starts it in the background before toolchain
setup so the ~90s OrbStack startup overlaps Python/Node/cache-restore steps.
v1.6.0+ panics on `macos-15-intel` (Skylake CPU check); v1.5.1 is pinned.
- Hosted macOS acceptance uses the `ci-hosted-macos` compose variant so
sidecars and browser-lab mutable data live in Docker-managed volumes instead of FUSE-backed host bind mounts. That avoids the filesystem regressions seen with Qdrant and Postgres while preserving the real `clawops` setup, launchd activation, and repo-local stack flows.
- `workflow_dispatch` can benchmark cache toggles for the supported hosted
macOS path without changing the required PR gate.
- The workflow stays declarative by delegating runtime setup, image warming,
diagnostics, and summary generation to executable helper scripts under `tests/scripts/`. Compose image availability is verified with bounded retries and heartbeat logging.
- Hosted macOS repo-local stack activation is reconciled once after any failed
  `up`: the helper removes partial containers, re-probes the Docker backend, and
  retries the idempotent activation. A repeated failure remains blocking.
- Diagnostic, summary, artifact, and final runner-cleanup steps are best-effort
  observers; they remain visible in the job log but cannot replace the scenario's
  pass/fail result. macOS artifacts include OrbStack unified logs, runtime version
  and status, Docker state, disk usage, and VM memory statistics.
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
- `.github/dependabot.yml` watches the `uv`, GitHub Actions, and shipped
  Docker Compose dependency surfaces on a low-noise weekly cadence.
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
dedicated release helper script, generates `strongclaw-release-manifest.json`
and `SHA256SUMS`, publishes or updates the GitHub release with `gh`, and emits
GitHub attestations for both build provenance and the generated SBOM.
- Release publishing uses the `release` environment and the
  [release break-glass runbook](./runbooks/release-break-glass.md) so a
  solo-maintainer emergency path is explicit and auditable.
- The active tag ruleset restricts creation of matching `v*` tags to the
  `jsugg` bypass actor and blocks normal update/deletion of released tags.
- `.github/workflows/upstream-merge-validation.yml` runs the repo quality gate
plus nightly validation steps after an upstream merge lands in the fork.
- `.github/workflows/memory-plugin-verification.yml` runs the dedicated
hypermemory Qdrant checks against the official pinned Qdrant GHCR image instead of Docker Hub.
- `.github/workflows/devflow-contract.yml` syncs the locked environment,
compile-checks the repo, runs targeted devflow tests, and validates `clawops devflow plan --goal "contract smoke"` without live ACP providers.
- Operators can verify published assets with `SHA256SUMS`, the release
manifest, and GitHub's attestation tooling after a tagged release lands.

Canonical plugin support status lives in [Plugin Inventory](./PLUGIN_INVENTORY.md).
