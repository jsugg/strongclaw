# Plugin Inventory

This page is the canonical StrongClaw plugin support matrix.

## Shipped Plugins

| Plugin | Purpose | Default status | Build/runtime expectation | CI coverage | Support level |
| --- | --- | --- | --- | --- | --- |
| `strongclaw-hypermemory` | StrongClaw-owned memory plugin that proxies `clawops hypermemory` and preserves OpenClaw memory tool names. | Enabled by default in the `hypermemory` profile; opt-in via standalone overlay `75-strongclaw-hypermemory.example.json5`. | Requires a valid rendered `configPath` plus a callable StrongClaw command (`python -m clawops` in managed overlays). Startup preflight runs `clawops hypermemory verify --json` before serving tools. | `tests/suites/integration/clawops/hypermemory/test_plugin.py` and `platform/plugins/strongclaw-hypermemory/test/openclaw-host-functional.mjs` in CI. | **Supported (StrongClaw-owned)** |
| `memory-lancedb-pro` (vendored) | Vendored upstream memory plugin for migration bridge and compatibility use cases. | Not default; enabled through `memory-lancedb-pro` profile. | Vendored bundle under `platform/plugins/memory-lancedb-pro`, rendered via managed profile overlays. | `tests/suites/unit/ci/test_memory_plugin_verification.py` and `.github/workflows/memory-plugin-verification.yml`. | **Supported (vendored bridge path)** |

## Support Policy

- Support labels in this table must match real CI evidence and workflow coverage.
- New plugin entries must update this table and `platform/docs/CI_AND_SECURITY.md` in the same change.
- If a plugin is experimental or unavailable by default, that status must be explicit in this table and in any setup docs that mention it.

## Related Docs

- [Hypermemory](./HYPERMEMORY.md)
- [CI and Security](./CI_AND_SECURITY.md)
- [Degradation Contract](./DEGRADATION.md)
