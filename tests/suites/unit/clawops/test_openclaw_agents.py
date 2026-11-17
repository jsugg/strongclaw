"""Unit coverage for shaping the `agents` block of rendered OpenClaw configs."""

from __future__ import annotations

from typing import cast

import pytest

from clawops.openclaw_agents import (
    agent_entries,
    default_agent_id,
    normalize_agent_roster,
    sync_model_policy,
)


def _model_policy(config: dict[str, object]) -> object:
    agents = cast(dict[str, dict[str, object]], config["agents"])
    return agents["defaults"].get("modelPolicy")


def _legacy_config() -> dict[str, object]:
    return {
        "agents": {
            "defaults": {"models": {"openai/gpt-5.4": {"alias": "gpt"}, "zai/glm-5": {}}},
            "list": [
                {"id": "admin", "default": True, "tools": {"profile": "coding"}},
                {"id": "reader", "workspace": "/w/reader"},
            ],
        }
    }


def test_normalize_agent_roster_matches_the_doctor_migration() -> None:
    """The roster becomes keyed entries and the default marker becomes the surface owner."""
    config = normalize_agent_roster(_legacy_config())

    assert config["agents"] == {
        "defaults": {
            "models": {"openai/gpt-5.4": {"alias": "gpt"}, "zai/glm-5": {}},
            "systemAgent": {"agentId": "admin"},
            "authInheritance": {"agentId": "admin"},
        },
        "entries": {
            "admin": {"tools": {"profile": "coding"}},
            "reader": {"workspace": "/w/reader"},
        },
        "ownership": "explicit",
    }
    assert config["talk"] == {"agentId": "admin"}


def test_normalize_agent_roster_keeps_current_values_and_explicit_owners() -> None:
    """Existing entries and explicit owners win over the legacy roster, as doctor's do."""
    config: dict[str, object] = {
        "agents": {
            "defaults": {"systemAgent": {"agentId": "ops"}},
            "entries": {"admin": {"tools": {"profile": "minimal"}}},
            "list": [{"id": "admin", "default": True, "tools": {"profile": "coding", "deny": []}}],
        }
    }

    normalize_agent_roster(config)

    assert config["agents"] == {
        "defaults": {
            "systemAgent": {"agentId": "ops"},
            "authInheritance": {"agentId": "admin"},
        },
        "entries": {"admin": {"tools": {"profile": "minimal", "deny": []}}},
    }


def test_normalize_agent_roster_leaves_current_and_ownerless_configs_alone() -> None:
    """A config without a legacy roster is untouched; an unmarked fleet gets no owner."""
    current: dict[str, object] = {"agents": {"entries": {"a": {}}}}
    assert normalize_agent_roster(current) == {"agents": {"entries": {"a": {}}}}

    unmarked = normalize_agent_roster({"agents": {"list": [{"id": "a"}, {"id": "b"}]}})
    assert unmarked == {"agents": {"entries": {"a": {}, "b": {}}, "ownership": "explicit"}}


@pytest.mark.parametrize(
    ("roster", "message"),
    [
        ("admin", "`agents.list` must be a list"),
        ([{"name": "no id"}], "string `id`"),
    ],
)
def test_normalize_agent_roster_rejects_malformed_rosters(
    roster: str | list[dict[str, str]], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        normalize_agent_roster({"agents": {"list": roster}})


def test_agent_entries_reads_both_shapes_and_returns_live_settings() -> None:
    legacy = _legacy_config()
    current = normalize_agent_roster(_legacy_config())

    assert list(agent_entries(legacy)) == ["admin", "reader"]
    assert list(agent_entries(current)) == ["admin", "reader"]
    agent_entries(current)["reader"]["model"] = "zai/glm-5"
    assert agent_entries(current)["reader"]["model"] == "zai/glm-5"
    assert agent_entries({}) == {}


def test_sync_model_policy_carries_the_registry_into_the_allowlist() -> None:
    """Registry models stay selectable; operator entries are kept; no registry means no policy."""
    config = sync_model_policy(_legacy_config())
    assert _model_policy(config) == {"allow": ["openai/gpt-5.4", "zai/glm-5"]}

    operator: dict[str, object] = {
        "agents": {
            "defaults": {
                "models": {"zai/glm-5": {}},
                "modelPolicy": {"allow": ["openai/*"]},
            }
        }
    }
    assert _model_policy(sync_model_policy(operator)) == {"allow": ["openai/*", "zai/glm-5"]}

    assert sync_model_policy({"agents": {"defaults": {}}}) == {"agents": {"defaults": {}}}


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"agents": {"defaults": {"systemAgent": {"agentId": "Ops"}}, "entries": {}}}, "ops"),
        ({"agents": {"entries": {"Coder": {}, "admin": {}}}}, "coder"),
        ({"agents": {"list": [{"id": "reader"}, {"id": "admin", "default": True}]}}, "admin"),
        ({}, "main"),
    ],
)
def test_default_agent_id_follows_ownership_then_legacy_rules(
    config: dict[str, object], expected: str
) -> None:
    assert default_agent_id(config) == expected
