# strongclaw vendor notes

- Source repo: `https://github.com/CortexReach/memory-lancedb-pro`
- Pinned release: `v1.1.0-beta.9`
- Upstream commit resolved from the release tag: `2ebba8e6b7b65bf38336199384d5ec8690701f6e`
- Vendored on: `2026-03-16`

## Compatibility stance

Strongclaw does not enable this plugin by default. The shipped local overlays keep:

- `sessionStrategy = "none"`
- `selfImprovement.enabled = false`
- `enableManagementTools = false`

This avoids the upstream `command:new` / `command:reset` typed-hook incompatibility tracked in upstream issue `#191` when running on OpenClaw `2026.3.13`.

- Strongclaw verifies the vendored bundle in Ubuntu CI because LanceDB `0.26.2`
  publishes Apple binaries for `darwin-arm64` but not `darwin-x64`. Intel macOS
  hosts should treat `.github/workflows/plugin-verification.yml` as the
  authoritative verification lane.

## Review notes

- The plugin is loaded from an absolute `plugins.load.paths` entry, which matches current OpenClaw plugin-loading guidance.
- The strongclaw-local profiles target Ollama's OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1`.
- `memory-v2` remains the migration source and corpus reference path; QMD plus the context service remain the repo-document retrieval lane.
