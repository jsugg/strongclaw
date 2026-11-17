"""Shape and read the ``agents`` block of StrongClaw-rendered OpenClaw configs.

StrongClaw overlays author each agent roster as an ``agents.list`` array, because overlay merging
replaces lists: the ACP profile swaps out the whole trust-zone roster. OpenClaw 2026.8 reads a
keyed ``agents.entries`` map instead and migrates ``agents.list`` at gateway startup by rewriting
``openclaw.json`` (``docs/gateway/configuration.md``, startup migration). Rendered configs therefore
carry the current shape, so the gateway never rewrites the config StrongClaw manages.

Standard library only: ``strongclaw_runtime`` imports this module, and CI helper scripts load that
on a bare runner Python before the project environment exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

type JsonObject = dict[str, object]

EXPLICIT_OWNERSHIP: Final[str] = "explicit"
LEGACY_DEFAULT_AGENT_ID: Final[str] = "main"


def _mapping(value: object) -> Mapping[str, object]:
    """Return *value* as a string-keyed mapping, or an empty one."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _merge(base: object, overlay: object) -> object:
    """Recursively merge *overlay* onto *base*; overlay values win (stdlib-only `deep_merge`)."""
    if isinstance(base, Mapping) and isinstance(overlay, Mapping):
        merged: JsonObject = dict(cast(Mapping[str, object], base))
        for key, value in cast(Mapping[str, object], overlay).items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    return overlay


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


def agent_entries(config: Mapping[str, object]) -> dict[str, JsonObject]:
    """Return each agent's settings keyed by id, from ``agents.entries`` or a legacy roster.

    The returned settings objects are the config's own, so callers may update them in place.
    """
    agents = _mapping(config.get("agents"))
    entries = agents.get("entries")
    if isinstance(entries, Mapping):
        return {
            str(agent_id): cast(JsonObject, settings)
            for agent_id, settings in cast(Mapping[object, object], entries).items()
            if isinstance(settings, dict)
        }
    roster = agents.get("list")
    if not isinstance(roster, list):
        return {}
    return {
        cast(str, agent["id"]): agent
        for agent in (
            cast(JsonObject, raw) for raw in cast(list[object], roster) if isinstance(raw, dict)
        )
        if isinstance(agent.get("id"), str)
    }


def normalize_agent_roster(config: JsonObject) -> JsonObject:
    """Rewrite a legacy ``agents.list`` roster into OpenClaw's keyed ``agents.entries`` shape.

    Mirrors the migration ``openclaw doctor --fix`` applies: entries are keyed by id (existing
    entries win), a multi-agent fleet is marked ``ownership: "explicit"``, and the retired
    ``default: true`` marker becomes the owner of the surfaces that used to follow the default
    agent: ambient system work, inherited credentials, and Talk.
    """
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return config
    agents_object = cast(JsonObject, agents)
    roster = agents_object.pop("list", None)
    if roster is None:
        return config
    if not isinstance(roster, list):
        raise TypeError(
            f"OpenClaw config `agents.list` must be a list, got {type(roster).__name__}"
        )
    entries = _child_object(agents_object, "entries")
    owner: str | None = None
    for raw in cast(list[object], roster):
        agent = _mapping(raw)
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not agent_id:
            raise TypeError("every OpenClaw `agents.list` entry must have a string `id`")
        if agent.get("default") is True and owner is None:
            owner = agent_id
        settings = {key: value for key, value in agent.items() if key not in {"id", "default"}}
        existing = entries.get(agent_id)
        entries[agent_id] = _merge(settings, existing) if existing is not None else settings
    if len(entries) > 1:
        agents_object.setdefault("ownership", EXPLICIT_OWNERSHIP)
    if owner is not None:
        defaults = _child_object(agents_object, "defaults")
        _child_object(defaults, "systemAgent").setdefault("agentId", owner)
        _child_object(defaults, "authInheritance").setdefault("agentId", owner)
        _child_object(config, "talk").setdefault("agentId", owner)
    return config


def sync_model_policy(config: JsonObject) -> JsonObject:
    """Keep ``agents.defaults.modelPolicy.allow`` covering every model in the defaults registry.

    Through OpenClaw 2026.6 the ``agents.defaults.models`` registry doubled as the model allowlist.
    2026.8 separates it into an explicit ``modelPolicy.allow``, where a missing or empty list
    allows any model, so the registry keys are carried over as ``openclaw doctor --fix`` does.
    """
    defaults = _mapping(_mapping(config.get("agents")).get("defaults"))
    registry = defaults.get("models")
    if not isinstance(registry, Mapping) or not registry:
        return config
    defaults_object = _child_object(_child_object(config, "agents"), "defaults")
    policy = _child_object(defaults_object, "modelPolicy")
    allow = policy.get("allow")
    if allow is not None and not isinstance(allow, list):
        raise TypeError("OpenClaw config `agents.defaults.modelPolicy.allow` must be a list")
    allowed = cast(list[object], allow) if allow is not None else []
    for model_ref in cast(Mapping[str, object], registry):
        if model_ref not in allowed:
            allowed.append(model_ref)
    policy["allow"] = allowed
    return config


def default_agent_id(config: Mapping[str, object]) -> str:
    """Return the agent that owns ambient work, normalized to lowercase.

    ``agents.defaults.systemAgent`` wins, then a legacy ``default: true`` marker, then the first
    agent (OpenClaw 2026.6's implicit default), and finally OpenClaw's historical ``main``.
    """
    defaults = _mapping(_mapping(config.get("agents")).get("defaults"))
    owner = _mapping(defaults.get("systemAgent")).get("agentId")
    if isinstance(owner, str) and owner.strip():
        return owner.strip().lower()
    entries = agent_entries(config)
    marked = next(
        (agent_id for agent_id, settings in entries.items() if settings.get("default") is True),
        None,
    )
    chosen = marked if marked is not None else next(iter(entries), None)
    return chosen.strip().lower() if chosen is not None else LEGACY_DEFAULT_AGENT_ID
