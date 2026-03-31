"""Tests for the opt-in StrongClaw hypermemory OpenClaw plugin bundle."""

from __future__ import annotations

import json

from tests.utils.helpers.repo import REPO_ROOT


def test_hypermemory_plugin_manifest_and_package_metadata() -> None:
    plugin_root = REPO_ROOT / "platform/plugins/strongclaw-hypermemory"
    manifest = json.loads((plugin_root / "openclaw.plugin.json").read_text(encoding="utf-8"))
    package = json.loads((plugin_root / "package.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "strongclaw-hypermemory"
    assert manifest["kind"] == "memory"
    assert "configPath" in manifest["configSchema"]["properties"]
    assert "autoCapture" in manifest["configSchema"]["properties"]
    assert "captureMinMessages" in manifest["configSchema"]["properties"]
    assert "startupTimeoutMs" in manifest["configSchema"]["properties"]
    assert "toolTimeoutMs" in manifest["configSchema"]["properties"]
    assert package["openclaw"]["extensions"] == ["./index.js"]
    assert package["scripts"]["test:openclaw-host"] == "node test/openclaw-host-functional.mjs"
    assert (plugin_root / "test" / "openclaw-host-functional.mjs").exists()
    assert (plugin_root / "test" / "helpers" / "openclaw-plugin-sdk-stub.mjs").exists()


def test_hypermemory_plugin_uses_compatible_tool_names() -> None:
    plugin_source = (REPO_ROOT / "platform/plugins/strongclaw-hypermemory/index.js").read_text(
        encoding="utf-8"
    )

    assert 'name: "memory_search"' in plugin_source
    assert 'name: "memory_get"' in plugin_source
    assert 'program.command("memory")' in plugin_source
    assert 'commands: ["memory"]' in plugin_source
    assert "--scope" in plugin_source
    assert "--mode" in plugin_source
    assert "before_prompt_build" in plugin_source
    assert "--backend" in plugin_source
    assert 'name: "memory_forget"' in plugin_source
    assert 'name: "memory_list_facts"' in plugin_source
    assert "autoCapture" in plugin_source
    assert "record-injection" in plugin_source
    assert "createStartupGate" in plugin_source
    assert "startup preflight" in plugin_source
