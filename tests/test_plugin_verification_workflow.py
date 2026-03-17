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
    assert 'PLUGIN_DIR="$ROOT/platform/plugins/memory-lancedb-pro"' in script
    assert "npm ci" in script
    assert "npm test" in script
    assert (
        'npm install --prefix "$tool_dir" --no-fund --no-audit "openclaw@${OPENCLAW_VERSION}"'
        in script
    )
    assert "npm run test:openclaw-host" in script
    assert "darwin-x64 native binary" in script


def test_plugin_verification_docs_describe_linux_ci_gate() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ci_doc = (repo_root / "platform/docs/CI_AND_SECURITY.md").read_text(encoding="utf-8")
    vendor_note = (
        repo_root / "platform/plugins/memory-lancedb-pro/STRONGCLAW_VENDOR.md"
    ).read_text(encoding="utf-8")

    assert "plugin-verification" in ci_doc
    assert "memory-lancedb-pro" in ci_doc
    assert "openclaw@2026.3.13" in ci_doc
    assert "darwin-x64" in ci_doc
    assert "darwin-x64" in vendor_note
    assert "Ubuntu CI" in vendor_note
