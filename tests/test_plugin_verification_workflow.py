"""Regression tests for vendored memory plugin verification surfaces."""

from __future__ import annotations

import pathlib


def test_plugin_verification_workflow_runs_vendored_memory_plugin_checks() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/plugin-verification.yml").read_text(encoding="utf-8")
    script = (repo_root / "scripts/ci/run_memory_plugin_verification.sh").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-node@53b83947a5a98c8d113130e565377fae1a50d02f" in workflow
    assert 'node-version: "24.13.1"' in workflow
    assert (
        "cache-dependency-path: platform/plugins/memory-lancedb-pro/package-lock.json" in workflow
    )
    assert "./scripts/ci/run_memory_plugin_verification.sh" in workflow

    assert 'OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"' in script
    assert '"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"' in script
    assert (
        'npm install --prefix "$tool_dir" --no-fund --no-audit "openclaw@${OPENCLAW_VERSION}"'
        in script
    )
    assert "npm run test:openclaw-host" in script
    assert "-u AWS_PROFILE" in script
    assert "-u AWS_REGION" in script


def test_plugin_verification_docs_describe_linux_ci_gate() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ci_doc = (repo_root / "platform/docs/CI_AND_SECURITY.md").read_text(encoding="utf-8")
    vendor_note = (
        repo_root / "platform/plugins/memory-lancedb-pro/STRONGCLAW_VENDOR.md"
    ).read_text(encoding="utf-8")

    assert "plugin-verification" in ci_doc
    assert "memory-lancedb-pro" in ci_doc
    assert "openclaw@2026.3.13" in ci_doc
    assert "@lancedb/lancedb@0.22.3" in ci_doc
    assert "AWS credential env vars" in ci_doc
    assert "darwin/x86_64" in vendor_note
    assert "@lancedb/lancedb@0.22.3" in vendor_note


def test_vendored_plugin_test_helper_supports_macos_and_linux_global_module_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    helper = (
        repo_root / "platform/plugins/memory-lancedb-pro/test/helpers/node-path.mjs"
    ).read_text(encoding="utf-8")
    runtime = (repo_root / "platform/plugins/memory-lancedb-pro/index.ts").read_text(
        encoding="utf-8"
    )
    cli_smoke = (repo_root / "platform/plugins/memory-lancedb-pro/test/cli-smoke.mjs").read_text(
        encoding="utf-8"
    )

    assert "/opt/homebrew/lib/node_modules" in helper
    assert "/usr/local/lib/node_modules" in helper
    assert "/usr/lib/node_modules" in helper
    assert "/opt/homebrew/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert "/usr/local/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert "/usr/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert 'import { initGlobalNodePath } from "./helpers/node-path.mjs";' in cli_smoke
