"""Wire the first-party OpenClaw plugins from the locked node-tools tree into rendered configs.

OpenClaw 2026.4+ moved providers, runtimes, and channels that older releases shipped built in
into separately published ``@openclaw/*`` plugins. Left alone, OpenClaw fetches them from npm
at an unpinned ``latest`` (``doctor --fix``, ``openclaw update``) or fails at runtime. StrongClaw
instead pins them in ``platform/node-tools`` and points ``plugins.load.paths`` at that tree, so
every plugin OpenClaw loads comes from the committed, integrity-checked lock.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable, Iterator, Mapping
from typing import Final, cast

type JsonObject = dict[str, object]

FIRST_PARTY_PLUGIN_SCOPE: Final[str] = "@openclaw/"
CODEX_AGENT_RUNTIME_ID: Final[str] = "codex"
ACPX_BACKEND_ID: Final[str] = "acpx"
BUNDLED_DISCOVERY_COMPAT: Final[str] = "compat"
# Provider plugins bundled with the pinned OpenClaw. They were core built-ins before
# 2026.4, so restrictive `plugins.allow` lists never had to name them.
BUNDLED_PROVIDER_PLUGIN_IDS: Final[frozenset[str]] = frozenset(
    {"anthropic", "github-copilot", "ollama", "openai", "openrouter"}
)


def _mapping(value: object) -> Mapping[str, object]:
    """Return *value* as a string-keyed mapping, or an empty one."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _agents(config: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the configured agent entries."""
    raw_list = _mapping(config.get("agents")).get("list")
    if not isinstance(raw_list, list):
        return []
    return [_mapping(agent) for agent in cast(list[object], raw_list)]


def _model_selection_refs(selection: object) -> Iterator[str]:
    """Yield the refs of one `model` value (a ref string or a primary/fallbacks object)."""
    if isinstance(selection, str):
        yield selection
        return
    mapping = _mapping(selection)
    primary = mapping.get("primary")
    if isinstance(primary, str):
        yield primary
    fallbacks = mapping.get("fallbacks")
    if isinstance(fallbacks, list):
        yield from (ref for ref in cast(list[object], fallbacks) if isinstance(ref, str))


def _model_maps(config: Mapping[str, object]) -> Iterator[Mapping[str, object]]:
    """Yield every per-model settings map (agent defaults first, then each agent)."""
    yield _mapping(_mapping(_mapping(config.get("agents")).get("defaults")).get("models"))
    for agent in _agents(config):
        yield _mapping(agent.get("models"))


def configured_model_refs(config: Mapping[str, object]) -> frozenset[str]:
    """Return every model ref the agent defaults and agents can select."""
    defaults = _mapping(_mapping(config.get("agents")).get("defaults"))
    refs = set(_model_selection_refs(defaults.get("model")))
    for agent in _agents(config):
        refs.update(_model_selection_refs(agent.get("model")))
    for model_map in _model_maps(config):
        refs.update(model_map)
    return frozenset(refs)


def configured_model_providers(config: Mapping[str, object]) -> frozenset[str]:
    """Return the provider ids of every configured model ref."""
    return frozenset(ref.split("/", 1)[0] for ref in configured_model_refs(config) if "/" in ref)


def _uses_provider(provider_id: str) -> Callable[[Mapping[str, object]], bool]:
    """Return a predicate matching configs that select a model from *provider_id*."""

    def predicate(config: Mapping[str, object]) -> bool:
        return provider_id in configured_model_providers(config)

    return predicate


def _uses_codex_runtime(config: Mapping[str, object]) -> bool:
    """Return whether any model is routed through the Codex agent runtime."""
    return any(
        _mapping(_mapping(settings).get("agentRuntime")).get("id") == CODEX_AGENT_RUNTIME_ID
        for model_map in _model_maps(config)
        for settings in model_map.values()
    )


def _uses_acpx_backend(config: Mapping[str, object]) -> bool:
    """Return whether the ACP runtime or any ACP agent uses the acpx backend."""
    if _mapping(config.get("acp")).get("backend") == ACPX_BACKEND_ID:
        return True
    for agent in _agents(config):
        runtime = _mapping(agent.get("runtime"))
        if runtime.get("type") == "acp" and (
            _mapping(runtime.get("acp")).get("backend") == ACPX_BACKEND_ID
        ):
            return True
    return False


def _uses_otel_diagnostics(config: Mapping[str, object]) -> bool:
    """Return whether OpenTelemetry diagnostics export is enabled."""
    return _mapping(_mapping(config.get("diagnostics")).get("otel")).get("enabled") is True


@dataclasses.dataclass(frozen=True, slots=True)
class FirstPartyPlugin:
    """One first-party OpenClaw plugin installed from the locked node-tools tree."""

    plugin_id: str
    package: str
    is_required: Callable[[Mapping[str, object]], bool]
    # Providers replace former OpenClaw built-ins and stay available like they used to;
    # runtimes, channels, and services load only when the config uses them.
    always_enabled: bool = False


FIRST_PARTY_PLUGINS: Final[tuple[FirstPartyPlugin, ...]] = (
    FirstPartyPlugin("acpx", "@openclaw/acpx", _uses_acpx_backend),
    FirstPartyPlugin("codex", "@openclaw/codex", _uses_codex_runtime),
    FirstPartyPlugin("diagnostics-otel", "@openclaw/diagnostics-otel", _uses_otel_diagnostics),
    FirstPartyPlugin(
        "moonshot", "@openclaw/moonshot-provider", _uses_provider("moonshot"), always_enabled=True
    ),
    FirstPartyPlugin("zai", "@openclaw/zai-provider", _uses_provider("zai"), always_enabled=True),
)


def _child_object(parent: JsonObject, key: str) -> JsonObject:
    """Return the JSON object at *key*, creating it when absent."""
    value = parent.get(key)
    if isinstance(value, dict):
        return cast(JsonObject, value)
    child: JsonObject = {}
    parent[key] = child
    return child


def _string_list(parent: JsonObject, key: str) -> list[object]:
    """Return the list at *key*, creating it when absent."""
    value = parent.get(key)
    if isinstance(value, list):
        return cast(list[object], value)
    created: list[object] = []
    parent[key] = created
    return created


def _append_unique(target: list[object], value: str) -> None:
    """Append *value* unless it is already present."""
    if value not in target:
        target.append(value)


def attach_first_party_plugins(config: JsonObject, *, node_modules_dir: pathlib.Path) -> JsonObject:
    """Load every locked first-party plugin from *node_modules_dir* and enable the needed ones.

    Each plugin is made discoverable so OpenClaw never installs it from npm. A plugin the config
    uses is enabled (usage overrides a stale `enabled: false`); an unused runtime, channel, or
    service plugin is disabled unless the operator already configured it. With a restrictive
    `plugins.allow`, the enabled plugins plus the bundled providers the agents use are
    allowlisted, and bundled discovery keeps OpenClaw's pre-2026.5 compatibility mode.
    """
    plugins = _child_object(config, "plugins")
    if plugins.get("enabled") is False:
        return config
    load_paths = _string_list(_child_object(plugins, "load"), "paths")
    allow = plugins.get("allow")
    allowlist = cast(list[object], allow) if isinstance(allow, list) and allow else None
    entries = _child_object(plugins, "entries")
    for plugin in FIRST_PARTY_PLUGINS:
        _append_unique(load_paths, (node_modules_dir / plugin.package).as_posix())
        used = (allowlist is not None and plugin.plugin_id in allowlist) or plugin.is_required(
            config
        )
        if used and _mapping(entries.get(plugin.plugin_id)).get("enabled") is False:
            del entries[plugin.plugin_id]
        if used or plugin.always_enabled:
            if allowlist is not None:
                _append_unique(allowlist, plugin.plugin_id)
        elif plugin.plugin_id not in entries:
            entries[plugin.plugin_id] = {"enabled": False}
    if allowlist is not None:
        for provider_id in sorted(configured_model_providers(config) & BUNDLED_PROVIDER_PLUGIN_IDS):
            _append_unique(allowlist, provider_id)
        plugins.setdefault("bundledDiscovery", BUNDLED_DISCOVERY_COMPAT)
    return config
