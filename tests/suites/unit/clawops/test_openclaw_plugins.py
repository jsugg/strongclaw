"""Unit coverage for wiring locked first-party OpenClaw plugins into rendered configs."""

from __future__ import annotations

import pathlib
from typing import cast

import pytest

from clawops.openclaw_plugins import (
    FIRST_PARTY_PLUGINS,
    attach_first_party_plugins,
    configured_model_providers,
)

_NODE_MODULES = pathlib.Path("/opt/strongclaw/node-tools/node_modules")
_ALL_PATHS = [(_NODE_MODULES / plugin.package).as_posix() for plugin in FIRST_PARTY_PLUGINS]


def _plugins(config: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], config["plugins"])


def _base_config(**plugins: object) -> dict[str, object]:
    return {
        "agents": {
            "defaults": {
                "model": {"primary": "openai/gpt-5.4", "fallbacks": ["zai/glm-5"]},
                "models": {"openai/gpt-5.4": {"agentRuntime": {"id": "codex"}}},
            },
            "list": [{"id": "admin"}],
        },
        "plugins": dict(plugins),
    }


def test_configured_model_providers_cover_defaults_agents_and_model_maps() -> None:
    """Every place an agent can pick a model contributes its provider."""
    config: dict[str, object] = {
        "agents": {
            "defaults": {"model": "openai/gpt-5.4", "models": {"anthropic/claude-x": {}}},
            "list": [
                {"id": "a", "model": {"primary": "zai/glm-5", "fallbacks": ["moonshot/kimi"]}},
                {"id": "b", "models": {"github-copilot/gpt-5.5": {}}},
            ],
        }
    }

    assert configured_model_providers(config) == {
        "openai",
        "anthropic",
        "zai",
        "moonshot",
        "github-copilot",
    }


def test_unrestricted_allowlist_loads_all_and_disables_unused_runtimes() -> None:
    """Every locked plugin is discoverable; unused runtime/service plugins stay off."""
    config = attach_first_party_plugins(_base_config(allow=[]), node_modules_dir=_NODE_MODULES)
    plugins = _plugins(config)

    assert cast(dict[str, object], plugins["load"])["paths"] == _ALL_PATHS
    assert plugins["allow"] == []
    assert plugins["entries"] == {
        "acpx": {"enabled": False},
        "diagnostics-otel": {"enabled": False},
    }
    assert "bundledDiscovery" not in plugins


def test_restrictive_allowlist_names_needed_plugins_and_bundled_providers() -> None:
    """A memory-only allowlist keeps the providers and runtimes the agents rely on."""
    config = _base_config(allow=["strongclaw-hypermemory"], load={"paths": ["/x/hypermemory"]})
    agents = cast(dict[str, object], config["agents"])
    agents["list"] = [
        {
            "id": "worker",
            "model": {"primary": "github-copilot/gpt-5.5"},
            "runtime": {"type": "acp", "acp": {"backend": "acpx"}},
        }
    ]

    plugins = _plugins(attach_first_party_plugins(config, node_modules_dir=_NODE_MODULES))

    assert cast(dict[str, object], plugins["load"])["paths"] == ["/x/hypermemory", *_ALL_PATHS]
    assert plugins["allow"] == [
        "strongclaw-hypermemory",
        "acpx",
        "codex",
        "moonshot",
        "zai",
        "github-copilot",
        "openai",
    ]
    assert plugins["entries"] == {"diagnostics-otel": {"enabled": False}}
    assert plugins["bundledDiscovery"] == "compat"


def test_usage_overrides_a_stale_disable_but_operator_entries_are_kept() -> None:
    """Re-attaching after a config change re-enables a now-used plugin only."""
    config = _base_config(
        entries={
            "codex": {"enabled": False},
            "diagnostics-otel": {"enabled": True, "config": {"x": 1}},
        }
    )

    plugins = _plugins(attach_first_party_plugins(config, node_modules_dir=_NODE_MODULES))

    assert plugins["entries"] == {
        "acpx": {"enabled": False},
        "diagnostics-otel": {"enabled": True, "config": {"x": 1}},
    }


@pytest.mark.parametrize(
    ("overlay", "plugin_id"),
    [
        ({"diagnostics": {"otel": {"enabled": True}}}, "diagnostics-otel"),
        ({"acp": {"backend": "acpx"}}, "acpx"),
    ],
)
def test_service_plugins_load_only_when_configured(
    overlay: dict[str, object], plugin_id: str
) -> None:
    """Runtime and service plugins are enabled exactly when their feature is configured."""
    config = {**_base_config(allow=["memory-core"]), **overlay}

    plugins = _plugins(attach_first_party_plugins(config, node_modules_dir=_NODE_MODULES))

    assert plugin_id in cast(list[str], plugins["allow"])
    assert plugin_id not in cast(dict[str, object], plugins["entries"])


def test_attach_is_idempotent_and_respects_disabled_plugins() -> None:
    """Rendering twice changes nothing, and `plugins.enabled: false` is left alone."""
    once = attach_first_party_plugins(_base_config(allow=["x"]), node_modules_dir=_NODE_MODULES)
    snapshot = repr(once)

    assert repr(attach_first_party_plugins(once, node_modules_dir=_NODE_MODULES)) == snapshot
    disabled = _base_config(enabled=False)
    assert attach_first_party_plugins(disabled, node_modules_dir=_NODE_MODULES) == _base_config(
        enabled=False
    )
