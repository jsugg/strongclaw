"""Contracts for agent sandboxing and the single unsandboxed ACP dispatcher.

Every rendered agent runs with `sandbox.mode: "all"` except `acp-dispatch`. OpenClaw refuses ACP
spawns from sandboxed sessions, so the dispatcher is the one host-side requester, and it must be
reachable only through the operator.admin `/acp spawn` command: never a routing target, never a
spawn target, and unable to run an ordinary model turn.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator, Mapping
from typing import Final, cast

import pytest

from clawops.openclaw_config import PROFILES, render_openclaw_profile
from tests.utils.helpers.repo import REPO_ROOT

DISPATCHER_ID: Final[str] = "acp-dispatch"
DISPATCHER_PROFILE: Final[str] = "acp"

type JsonObject = dict[str, object]


def _render(profile_name: str) -> JsonObject:
    return render_openclaw_profile(
        profile_name=profile_name,
        repo_root=REPO_ROOT,
        home_dir=pathlib.Path("/home/contract"),
        user_timezone="UTC",
    )


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _agents(config: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = _mapping(config.get("agents")).get("list")
    return [_mapping(agent) for agent in cast(list[object], raw)] if isinstance(raw, list) else []


def _effective_sandbox_mode(config: Mapping[str, object], agent: Mapping[str, object]) -> object:
    own = _mapping(agent.get("sandbox")).get("mode")
    if own is not None:
        return own
    defaults = _mapping(_mapping(config.get("agents")).get("defaults"))
    return _mapping(defaults.get("sandbox")).get("mode")


def _dispatcher(config: Mapping[str, object]) -> Mapping[str, object]:
    return next(agent for agent in _agents(config) if agent.get("id") == DISPATCHER_ID)


def _string_list(value: object) -> list[str]:
    return [str(item) for item in cast(list[object], value)] if isinstance(value, list) else []


def _spawn_allowlists(config: Mapping[str, object]) -> Iterator[tuple[str, list[str]]]:
    defaults = _mapping(_mapping(config.get("agents")).get("defaults"))
    yield "agents.defaults", _string_list(_mapping(defaults.get("subagents")).get("allowAgents"))
    for agent in _agents(config):
        allow = _string_list(_mapping(agent.get("subagents")).get("allowAgents"))
        yield f"agents.list[{agent.get('id')}]", allow


@pytest.mark.parametrize("profile_name", sorted(PROFILES))
def test_only_the_acp_dispatcher_runs_unsandboxed(profile_name: str) -> None:
    """Any agent other than the dispatcher that is not fully sandboxed fails the contract."""
    config = _render(profile_name)
    unsandboxed = {
        str(agent.get("id")): _effective_sandbox_mode(config, agent)
        for agent in _agents(config)
        if agent.get("id") != DISPATCHER_ID and _effective_sandbox_mode(config, agent) != "all"
    }

    assert unsandboxed == {}, f"{profile_name}: agents outside sandbox.mode 'all': {unsandboxed}"


def test_the_dispatcher_exists_only_in_the_acp_profile_and_is_unsandboxed() -> None:
    """The dispatcher is the acp profile's ACP requester and appears nowhere else."""
    for profile_name in sorted(PROFILES):
        ids = [agent.get("id") for agent in _agents(_render(profile_name))]
        assert (DISPATCHER_ID in ids) == (profile_name == DISPATCHER_PROFILE), profile_name
        assert ids.count(DISPATCHER_ID) <= 1, profile_name

    config = _render(DISPATCHER_PROFILE)
    assert _effective_sandbox_mode(config, _dispatcher(config)) == "off"


def test_the_dispatcher_is_reachable_only_through_admin_acp_spawn() -> None:
    """Fail if any non-admin route can deliver work to the dispatcher."""
    config = _render(DISPATCHER_PROFILE)
    dispatcher = _dispatcher(config)
    agents = _agents(config)

    # Default routing: unrouted channel and CLI traffic lands on the default agent, which is the
    # one marked `default: true`, else the first entry.
    assert dispatcher.get("default") is not True
    assert agents[0].get("id") != DISPATCHER_ID
    # Channel routing and ACP conversation bindings.
    bound = [
        binding
        for binding in cast(list[object], config.get("bindings") or [])
        if _mapping(binding).get("agentId") == DISPATCHER_ID
    ]
    assert bound == [], bound
    # Agent-to-agent delegation: no agent (or the defaults) may spawn it, explicitly or by "*".
    for scope, allow in _spawn_allowlists(config):
        assert DISPATCHER_ID not in allow and "*" not in allow, f"{scope}: {allow}"
    # A direct operator turn (`openclaw agent --agent acp-dispatch`) cannot be closed by config,
    # so it must fail closed: an explicit allowlist that leaves no callable tool makes OpenClaw
    # stop the run before the prompt reaches the model. `/acp spawn` is a gateway command that
    # runs before any agent turn and is gated on operator.admin by OpenClaw itself.
    tools = _mapping(dispatcher.get("tools"))
    assert _string_list(tools.get("allow")), "the dispatcher needs an explicit tool allowlist"
    assert "*" in _string_list(tools.get("deny")), "the dispatcher must deny every tool"
    assert not _mapping(dispatcher.get("runtime")), "the dispatcher is not itself an ACP agent"


def test_acp_spawns_are_limited_to_the_worker_harnesses() -> None:
    """`/acp spawn` may only start the harnesses the acp profile's worker agents run."""
    config = _render(DISPATCHER_PROFILE)
    workers = {
        str(_mapping(_mapping(agent.get("runtime")).get("acp")).get("agent"))
        for agent in _agents(config)
        if _mapping(agent.get("runtime")).get("type") == "acp"
    }

    assert sorted(_string_list(_mapping(config.get("acp")).get("allowedAgents"))) == sorted(workers)
