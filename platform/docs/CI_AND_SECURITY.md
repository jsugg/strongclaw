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
- `strongclaw-memory-v2` host-functional checks through the real OpenClaw CLI boundary
- tagged release builds with artifact verification, GitHub Release assets, build provenance, and SBOM attestations
- Upstream Integration Validation

## Vendored plugin verification

The vendored `platform/plugins/memory-lancedb-pro` bundle is verified on
GitHub Actions in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `scripts/ci/verify_vendored_memory_plugin.sh`.
- That flow reuses `scripts/bootstrap/bootstrap_memory_plugin.sh`, which
  auto-detects the host and installs the default LanceDB dependency on
  supported hosts or the Intel-macOS fallback `@lancedb/lancedb@0.22.3`.
- The script then installs the pinned
  `openclaw@2026.3.13` CLI into a temporary tool directory, and then runs
  `npm run test:openclaw-host`.
- The host-functional step clears ambient AWS credential env vars first so
  local Bedrock model discovery noise does not contaminate test assertions.

## strongclaw-memory-v2 host verification

The repo-local `platform/plugins/strongclaw-memory-v2` bundle is also verified
in `.github/workflows/memory-plugin-verification.yml`.

- The shared entrypoint is `scripts/ci/verify_strongclaw_memory_v2_plugin.sh`.
- That flow installs the pinned `openclaw@2026.3.13` CLI into a temporary tool
  directory and runs `npm run test:openclaw-host` inside the plugin bundle.
- The host-functional test creates a temporary sqlite-backed `memory-v2`
  config, loads the plugin through an OpenClaw profile, and exercises
  `openclaw memory-v2 search` plus `openclaw memory-v2 get`, while confirming
  the built-in `openclaw memory ...` surface does not silently proxy to the
  plugin.

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
