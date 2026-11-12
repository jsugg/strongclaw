"""Keep the OpenClaw bundled plugins a rendered StrongClaw config relies on enabled.

OpenClaw ships some runtimes and providers as bundled plugins. A bundled plugin that is off by
default (the ``acpx`` ACP runtime) stays off unless the config enables it, and any non-empty
``plugins.allow`` disables every plugin it omits, including the bundled providers OpenClaw
otherwise enables by default. The memory profiles allowlist only their own plugins, so the
renderer adds back what the rest of the rendered config depends on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

type JsonObject = dict[str, object]

ACPX_PLUGIN_ID: Final[str] = "acpx"
ACPX_COMMAND: Final[str] = "acpx"
# Bundled provider plugins behind the model-auth paths StrongClaw documents (OLLAMA_API_KEY).
BUNDLED_PROVIDER_PLUGIN_IDS: Final[frozenset[str]] = frozenset({"ollama"})


def _mapping(value: object) -> Mapping[str, object]:
    """Return *value* as a string-keyed mapping, or an empty one."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def uses_acpx_backend(config: Mapping[str, object]) -> bool:
    """Return whether the ACP runtime or any ACP agent uses the acpx backend."""
    if _mapping(config.get("acp")).get("backend") == ACPX_PLUGIN_ID:
        return True
    agents = _mapping(config.get("agents")).get("list")
    for agent in cast(list[object], agents) if isinstance(agents, list) else []:
        runtime = _mapping(_mapping(agent).get("runtime"))
        if (
            runtime.get("type") == "acp"
            and _mapping(runtime.get("acp")).get("backend") == ACPX_PLUGIN_ID
        ):
            return True
    return False


def _child_object(parent: JsonObject, key: str) -> JsonObject:
    """Return the JSON object at *key*, creating it when absent."""
    value = parent.get(key)
    if isinstance(value, dict):
        return cast(JsonObject, value)
    if value is not None:
        raise TypeError(f"OpenClaw config `{key}` must be an object, got {type(value).__name__}")
    child: JsonObject = {}
    parent[key] = child
    return child


def _enable_acpx(plugins: JsonObject, *, acpx_version: str) -> None:
    """Enable the bundled acpx runtime on the locked acpx CLI instead of a runtime install."""
    entry = _child_object(_child_object(plugins, "entries"), ACPX_PLUGIN_ID)
    entry["enabled"] = True
    settings = _child_object(entry, "config")
    # Without a command, the plugin npm-installs its own acpx pin into the OpenClaw tree.
    settings.setdefault("command", ACPX_COMMAND)
    settings.setdefault("expectedVersion", acpx_version)


def attach_bundled_plugins(config: JsonObject, *, acpx_version: str) -> JsonObject:
    """Enable the bundled plugins *config* depends on and keep them inside `plugins.allow`."""
    plugins = _child_object(config, "plugins")
    if plugins.get("enabled") is False:
        return config
    required: list[str] = []
    if uses_acpx_backend(config):
        _enable_acpx(plugins, acpx_version=acpx_version)
        required.append(ACPX_PLUGIN_ID)
    allow = plugins.get("allow")
    if isinstance(allow, list) and allow:
        allowlist = cast(list[object], allow)
        for plugin_id in (*required, *sorted(BUNDLED_PROVIDER_PLUGIN_IDS)):
            if plugin_id not in allowlist:
                allowlist.append(plugin_id)
    return config
