"""Regression tests for vendored memory plugin verification surfaces."""

from __future__ import annotations

import pathlib


def test_plugin_verification_workflow_runs_vendored_memory_plugin_checks() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/memory-plugin-verification.yml").read_text(
        encoding="utf-8"
    )
    script = (repo_root / "scripts/ci/verify_vendored_memory_plugin.sh").read_text(encoding="utf-8")

    assert "name: Memory Plugin Integration Checks" in workflow
    assert "name: Run Memory Plugin Integration Checks" in workflow
    assert "./scripts/ci/verify_vendored_memory_plugin.sh" in workflow

    assert '"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"' in script
    assert "npm run test:openclaw-host" in script


def test_plugin_verification_workflow_runs_strongclaw_memory_host_checks() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/memory-plugin-verification.yml").read_text(
        encoding="utf-8"
    )
    script = (repo_root / "scripts/ci/verify_strongclaw_hypermemory_plugin.sh").read_text(
        encoding="utf-8"
    )

    assert "Run strongclaw-hypermemory Host Checks" in workflow
    assert "./scripts/ci/verify_strongclaw_hypermemory_plugin.sh" in workflow
    assert 'PLUGIN_DIR="$ROOT/platform/plugins/strongclaw-hypermemory"' in script
    assert "npm run test:openclaw-host" in script


def test_plugin_verification_docs_reference_current_workflow_and_script() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ci_doc = (repo_root / "platform/docs/CI_AND_SECURITY.md").read_text(encoding="utf-8")
    vendor_note = (
        repo_root / "platform/plugins/memory-lancedb-pro/STRONGCLAW_VENDOR.md"
    ).read_text(encoding="utf-8")

    assert "Memory Plugin Integration Checks" in ci_doc
    assert "verify_vendored_memory_plugin.sh" in ci_doc
    assert "verify_strongclaw_hypermemory_plugin.sh" in ci_doc
    assert "verify_vendored_memory_plugin.sh" in vendor_note


def test_vendored_plugin_host_checks_support_macos_and_linux_global_module_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    runtime = (repo_root / "platform/plugins/memory-lancedb-pro/index.ts").read_text(
        encoding="utf-8"
    )
    cli_smoke = (repo_root / "platform/plugins/memory-lancedb-pro/test/cli-smoke.mjs").read_text(
        encoding="utf-8"
    )

    assert "/opt/homebrew/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert "/usr/local/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert "/usr/lib/node_modules/openclaw/dist/extensionAPI.js" in runtime
    assert "/opt/homebrew/lib/node_modules" in cli_smoke
    assert "/usr/local/lib/node_modules" in cli_smoke
    assert "/usr/lib/node_modules" in cli_smoke
