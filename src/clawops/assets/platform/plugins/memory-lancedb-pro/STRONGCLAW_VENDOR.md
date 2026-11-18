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

This avoids the upstream `command:new` / `command:reset` typed-hook incompatibility tracked in upstream issue `#191` when running on OpenClaw `2026.9.37`.

- Strongclaw auto-detects `darwin/x86_64` and installs the newest stable
compatible LanceDB fallback, `@lancedb/lancedb@0.22.3`, because LanceDB `0.26.2` publishes Apple binaries for `darwin-arm64` but not `darwin-x64`.
- The shared verification path still runs in GitHub Actions and through
`scripts/ci/verify_vendored_memory_plugin.sh`, but Intel macOS hosts now use the same compatibility matrix locally instead of hard-failing.

## Local patches

- `index.ts` runs memory reflection through the host-injected
  `api.runtime.agent.runEmbeddedAgent` and keeps the upstream `dist/extensionAPI.js` import
  probe only as a fallback for hosts without it. OpenClaw 2026.8.35 no longer ships
  `dist/extensionAPI.js` (the deprecated `openclaw/extension-api` bridge, whose 2026.6 warning
  pointed plugins at `api.runtime.agent.*`), so the probe alone finds no runner.
- Reflection and the `systemSessionMemory` hook read the reset session through
  `openclaw/plugin-sdk/session-transcript-runtime` when no transcript file exists: OpenClaw 2026.8
  keeps runtime transcripts in the agent's SQLite database, so the upstream JSONL recovery
  finds nothing. The file path still serves older hosts.
- The `test/*.mjs` `NODE_PATH` lists add `<prefix>/lib/strongclaw-node-tools/current/node_modules`
  next to each `<prefix>/lib/node_modules/openclaw/node_modules` entry: `npm ci` hoists OpenClaw's
  dependencies there instead of nesting them under the package as `npm install -g` did.
- `openclaw.plugin.json` declares `contracts.tools`. OpenClaw 2026.5+ discovers plugin tools
  from that manifest list and drops `api.registerTool(...)` registrations it does not name.
- `test/openclaw-host-functional.mjs` runs `plugins info --runtime`: OpenClaw 2026.5+ lists runtime
  registrations such as the `memory-pro` CLI command only when the runtime is loaded.
- `cli.ts` writes `--json` documents (`list`, `search`, `stats`) with `process.stdout.write`.
  OpenClaw 2026.5+ routes plugin `console.log` output to stderr while a command runs in `--json`
  mode, which left `memory-pro search --json` with nothing but log lines on stdout. The host test
  now reads `--json` results from stdout alone instead of merging both streams.

## Review notes

- The plugin is loaded from an absolute `plugins.load.paths` entry, which matches current OpenClaw plugin-loading guidance.
- The strongclaw-managed `memory-lancedb-pro` profile targets Ollama's OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1`.
- `hypermemory` remains the migration source and corpus reference path; QMD plus the context service remain the repo-document retrieval lane.
