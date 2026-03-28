# strongclaw vendor notes

- Source repo: `https://github.com/CortexReach/memory-lancedb-pro`
- Pinned release: `v1.1.0-beta.10`
- Upstream commit resolved from the release tag: `63495671fde55f2c8e3d6eb95267381d1889cca9`
- Vendored on: `2026-03-24`

## Compatibility stance

Strongclaw does not enable this plugin by default. The shipped local overlays keep:

- `autoRecall = false`
- `sessionStrategy = "none"`
- `selfImprovement.enabled = false`
- `enableManagementTools = false`

This avoids the upstream `command:new` / `command:reset` typed-hook incompatibility tracked in upstream issue `#191` when running on OpenClaw `2026.3.13`.

- Strongclaw auto-detects `darwin/x86_64` and installs the newest stable
compatible LanceDB fallback, `@lancedb/lancedb@0.22.3`, because LanceDB `0.26.2` publishes Apple binaries for `darwin-arm64` but not `darwin-x64`.
- The shared verification path still runs in GitHub Actions and through
`scripts/ci/verify_vendored_memory_plugin.sh`, but Intel macOS hosts now use the same compatibility matrix locally instead of hard-failing.

## Review notes

- The plugin is loaded from an absolute `plugins.load.paths` entry, which matches current OpenClaw plugin-loading guidance.
- The strongclaw-managed `memory-lancedb-pro` profile targets Ollama's OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1`.
- `hypermemory` remains the migration source and corpus reference path; QMD plus the context service remain the repo-document retrieval lane.
