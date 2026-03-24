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
- tagged release builds with artifact verification, GitHub Release assets, build provenance, and SBOM attestations
- Upstream Integration Validation

## Vendored plugin verification

The vendored `platform/plugins/memory-lancedb-pro` bundle is verified on
GitHub Actions in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `the vendored-memory plugin verification workflow`.
- That flow reuses `clawops config memory --set-profile memory-lancedb-pro`, which
  auto-detects the host and installs the default LanceDB dependency on
  supported hosts or the Intel-macOS fallback `@lancedb/lancedb@0.22.3`.
- The script then installs the pinned
  `openclaw@2026.3.13` CLI into a temporary tool directory, and then runs
  `npm run test:openclaw-host`.
- The host-functional step clears ambient AWS credential env vars first so
  local Bedrock model discovery noise does not contaminate test assertions.

## strongclaw-hypermemory host verification

The repo-local `platform/plugins/strongclaw-hypermemory` bundle is also verified
in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `the strongclaw-hypermemory verification workflow`.
- That flow runs `npm run test:openclaw-host` inside the plugin bundle.
- The host-functional test creates a temporary sqlite-backed `hypermemory`
  config, registers the plugin through the local SDK stub, verifies the
  exported `memory` CLI surface and subcommands, and exercises the
  strongclaw-owned `memory_search` and `memory_get` tool paths.

## Policy for new code

- no direct secrets in config
- new skills/plugins require scan + review
- harness cases should be added for new security-sensitive behavior
- browser-lab changes need explicit review

## Dependency and release provenance

- `.github/workflows/dependency-submission.yml` generates `sbom.spdx.json` with
  `anchore/sbom-action` and submits the resulting dependency snapshot to the
  GitHub dependency graph.
- `.github/workflows/release.yml` syncs the locked `uv` dev environment, builds
  the Python sdist/wheel, verifies each artifact with `twine check` plus fresh
  install smoke tests, publishes the release assets with `gh release create`,
  and emits GitHub attestations for both build provenance and the generated
  SBOM.
- `.github/workflows/upstream-merge-validation.yml` runs the repo quality gate
  plus nightly validation steps after an upstream merge lands in the fork.
- Operators can verify published provenance with GitHub's attestation tooling
  after a tagged release lands.
