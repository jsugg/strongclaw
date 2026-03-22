"""Tests for the opt-in StrongClaw hypermemory OpenClaw plugin bundle."""

from __future__ import annotations

import json
import pathlib


def test_hypermemory_plugin_manifest_and_package_metadata() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    plugin_root = repo_root / "platform/plugins/strongclaw-hypermemory"
    manifest = json.loads((plugin_root / "openclaw.plugin.json").read_text(encoding="utf-8"))
    package = json.loads((plugin_root / "package.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "strongclaw-hypermemory"
    assert manifest["kind"] == "memory"
    assert "configPath" in manifest["configSchema"]["properties"]
    assert package["openclaw"]["extensions"] == ["./index.js"]
    assert package["scripts"]["test:openclaw-host"] == "node test/openclaw-host-functional.mjs"
    assert (plugin_root / "test" / "openclaw-host-functional.mjs").exists()
    assert (plugin_root / "test" / "helpers" / "openclaw-plugin-sdk-stub.mjs").exists()


def test_hypermemory_plugin_uses_compatible_tool_names() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    plugin_source = (repo_root / "platform/plugins/strongclaw-hypermemory/index.js").read_text(
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
