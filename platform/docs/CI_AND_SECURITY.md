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
Ubuntu in `.github/workflows/plugin-verification.yml`.

- The shared entrypoint is `scripts/ci/run_memory_plugin_verification.sh`.
- The script runs `npm ci`, `npm test`, installs the pinned
  `openclaw@2026.3.13` CLI into a temporary tool directory, and then runs
  `npm run test:openclaw-host`.
- Linux CI is intentional: LanceDB `0.26.2` does not publish a
  `darwin-x64` native package, so Intel macOS hosts should use the GitHub
  Actions lane instead of expecting the vendored suite to pass locally.

## Policy for new code

- no direct secrets in config
- new skills/plugins require scan + review
- harness cases should be added for new security-sensitive behavior
- browser-lab changes need explicit review
