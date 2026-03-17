# CI and Security

The repository includes:

- CodeQL
- Semgrep
- Gitleaks
- Trivy
- harness smoke
- nightly regression
- `plugin-verification` for the vendored `memory-lancedb-pro` bundle (`npm test` plus `openclaw@2026.3.13` host-functional coverage)
- upstream merge gate

## Vendored plugin verification

The vendored `platform/plugins/memory-lancedb-pro` bundle is verified on
GitHub Actions in `.github/workflows/plugin-verification.yml`.

- The shared entrypoint is `scripts/ci/run_memory_plugin_verification.sh`.
- That flow reuses `scripts/bootstrap/bootstrap_memory_plugin.sh`, which
  auto-detects the host and installs the default LanceDB dependency on
  supported hosts or the Intel-macOS fallback `@lancedb/lancedb@0.22.3`.
- The script then installs the pinned
  `openclaw@2026.3.13` CLI into a temporary tool directory, and then runs
  `npm run test:openclaw-host`.
- The host-functional step clears ambient AWS credential env vars first so
  local Bedrock model discovery noise does not contaminate test assertions.

## Policy for new code

- no direct secrets in config
- new skills/plugins require scan + review
- harness cases should be added for new security-sensitive behavior
- browser-lab changes need explicit review
