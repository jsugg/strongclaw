"""Unit tests for keeping OpenClaw bundled plugins enabled in rendered configs."""

from __future__ import annotations

import pathlib
from typing import cast

import pytest

from clawops.openclaw_config import render_openclaw_profile
from clawops.openclaw_plugins import attach_bundled_plugins, uses_acpx_backend
from clawops.platform_compat import DEFAULT_ACPX_VERSION
from tests.utils.helpers.repo import REPO_ROOT

type JsonObject = dict[str, object]


def _plugins(config: JsonObject) -> JsonObject:
    return cast(JsonObject, config["plugins"])


def _acp_agent(backend: str) -> JsonObject:
    return {"id": "coder", "runtime": {"type": "acp", "acp": {"backend": backend}}}


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"agents": {"list": [_acp_agent("acpx")]}}, True),
        ({"acp": {"backend": "acpx"}}, True),
        ({"agents": {"list": [_acp_agent("other")]}}, False),
        ({"agents": {"list": [{"id": "admin"}]}}, False),
        ({}, False),
    ],
)
def test_uses_acpx_backend_detects_runtime_and_agent_backends(
    config: JsonObject, expected: bool
) -> None:
    assert uses_acpx_backend(config) is expected


def test_attach_enables_acpx_on_the_locked_cli_for_acpx_agents() -> None:
    config: JsonObject = {
        "agents": {"list": [_acp_agent("acpx")]},
        "plugins": {"entries": {"acpx": {"enabled": False}}},
    }

    attach_bundled_plugins(config, acpx_version="9.9.9")

    assert config["plugins"] == {
        "entries": {
            "acpx": {"enabled": True, "config": {"command": "acpx", "expectedVersion": "9.9.9"}}
        }
    }


def test_attach_keeps_operator_acpx_settings() -> None:
    config: JsonObject = {
        "acp": {"backend": "acpx"},
        "plugins": {"entries": {"acpx": {"config": {"command": "/opt/acpx", "cwd": "/w"}}}},
    }

    attach_bundled_plugins(config, acpx_version="9.9.9")

    acpx = cast(JsonObject, cast(JsonObject, _plugins(config)["entries"])["acpx"])
    assert acpx["config"] == {
        "command": "/opt/acpx",
        "cwd": "/w",
        "expectedVersion": "9.9.9",
    }


def test_attach_leaves_acpx_alone_when_nothing_uses_it() -> None:
    config: JsonObject = {"agents": {"list": [{"id": "admin"}]}}

    attach_bundled_plugins(config, acpx_version="9.9.9")

    assert config["plugins"] == {}


def test_attach_extends_a_restrictive_allowlist_with_required_and_provider_plugins() -> None:
    config: JsonObject = {
        "agents": {"list": [_acp_agent("acpx")]},
        "plugins": {"allow": ["memory-lancedb-pro", "ollama"]},
    }

    attach_bundled_plugins(config, acpx_version="9.9.9")

    assert _plugins(config)["allow"] == ["memory-lancedb-pro", "ollama", "acpx"]


def test_attach_does_not_create_an_allowlist() -> None:
    config: JsonObject = {"plugins": {"allow": []}}

    attach_bundled_plugins(config, acpx_version="9.9.9")

    assert config["plugins"] == {"allow": []}


def test_attach_respects_globally_disabled_plugins() -> None:
    config: JsonObject = {"agents": {"list": [_acp_agent("acpx")]}, "plugins": {"enabled": False}}

    attach_bundled_plugins(config, acpx_version="9.9.9")

    assert config["plugins"] == {"enabled": False}


@pytest.mark.parametrize("profile_name", ["hypermemory", "memory-lancedb-pro"])
def test_restrictive_memory_profiles_keep_the_bundled_ollama_provider(profile_name: str) -> None:
    rendered = render_openclaw_profile(
        profile_name=profile_name,
        repo_root=REPO_ROOT,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert "ollama" in rendered["plugins"]["allow"]


def test_acp_profile_enables_the_bundled_acpx_runtime() -> None:
    rendered = render_openclaw_profile(
        profile_name="acp",
        repo_root=REPO_ROOT,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["plugins"]["entries"]["acpx"] == {
        "enabled": True,
        "config": {"command": "acpx", "expectedVersion": DEFAULT_ACPX_VERSION},
    }
