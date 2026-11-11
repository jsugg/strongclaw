"""Model auth and gateway features against the real OpenClaw runtime and the fake model server."""

from __future__ import annotations

import time
import uuid
from typing import cast

from tests.utils.helpers.feature_verification import (
    FAKE_CHAT_MODEL_REF,
    REPLY_PREFIX,
    FeatureHost,
    GatewayController,
    gateway_status,
    plugin_statuses,
)

TURN_TIMEOUT_SECONDS = 300


def _agent_ids(feature_host: FeatureHost) -> list[str]:
    agents = cast(dict[str, object], feature_host.rendered_config()["agents"])
    return [
        str(cast(dict[str, object], agent)["id"]) for agent in cast(list[object], agents["list"])
    ]


def _token(label: str) -> str:
    return f"FV-{label}-{uuid.uuid4().hex[:10]}"


def _requests_since(feature_host: FeatureHost, started: float) -> list[dict[str, object]]:
    return [
        request
        for request in feature_host.fake_model_requests()
        if float(cast(float, request.get("at", 0.0))) >= started
    ]


def test_agents_list_matches_rendered_config(feature_host: FeatureHost) -> None:
    payload = feature_host.openclaw("agents", "list", "--json", timeout=120).require_ok().json()
    listed = [str(cast(dict[str, object], agent)["id"]) for agent in cast(list[object], payload)]
    assert sorted(listed) == sorted(_agent_ids(feature_host)), (listed, _agent_ids(feature_host))


def test_model_auth_ensure_applies_env_driven_chain(feature_host: FeatureHost) -> None:
    outcome = feature_host.clawops("model-auth", "--asset-root", ".", "ensure", timeout=600)
    payload = cast(dict[str, object], outcome.require_ok().json())
    assert payload["ok"] is True and payload["missingAgents"] == [], payload
    agents = cast(dict[str, object], feature_host.rendered_config()["agents"])
    defaults = cast(dict[str, object], agents["defaults"])
    assert cast(dict[str, object], defaults["model"])["primary"] == FAKE_CHAT_MODEL_REF
    for raw_agent in cast(list[object], agents["list"]):
        agent = cast(dict[str, object], raw_agent)
        assert cast(dict[str, object], agent["model"])["primary"] == FAKE_CHAT_MODEL_REF, agent


def test_model_probe_reaches_the_provider(feature_host: FeatureHost) -> None:
    started = time.time()
    outcome = feature_host.clawops(
        "model-auth",
        "--asset-root",
        ".",
        "check",
        "--probe",
        "--probe-max-tokens",
        "8",
        timeout=900,
    )
    payload = cast(dict[str, object], outcome.require_ok().json())
    assert payload["ok"] is True and payload["checkedOnly"] is True, payload
    probes = [
        request
        for request in _requests_since(feature_host, started)
        if request.get("path") == "/v1/chat/completions"
    ]
    assert len(probes) >= len(
        _agent_ids(feature_host)
    ), f"expected one live probe per agent, fake provider saw {len(probes)}"


def test_models_list_reports_available_models(feature_host: FeatureHost) -> None:
    for agent_id in _agent_ids(feature_host):
        payload = (
            feature_host.openclaw("models", "--agent", agent_id, "list", "--json", timeout=120)
            .require_ok()
            .json()
        )
        models = cast(list[dict[str, object]], cast(dict[str, object], payload)["models"])
        available = [model for model in models if model.get("available") is True]
        assert available, f"agent {agent_id} has no available model: {models}"


def test_gateway_status_reports_loopback_rpc(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    del gateway
    status = gateway_status(feature_host)
    rpc = cast(dict[str, object], status["rpc"])
    bind = cast(dict[str, object], status["gateway"])
    assert rpc["ok"] is True, status
    assert bind["bindMode"] == "loopback" and bind["bindHost"] == "127.0.0.1", bind


def test_gateway_runs_an_agent_turn_through_the_provider(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    del gateway
    token = _token("turn")
    started = time.time()
    outcome = feature_host.openclaw(
        "agent",
        "--agent",
        _agent_ids(feature_host)[0],
        "--session-id",
        f"featver-{token}",
        "--message",
        f"Feature verification turn {token}",
        "--json",
        timeout=TURN_TIMEOUT_SECONDS,
    ).require_ok()
    turns = [
        request
        for request in _requests_since(feature_host, started)
        if token in str(request.get("latestUser", ""))
    ]
    assert turns, f"the gateway never sent the turn to the provider\n{outcome.describe()}"
    assert f"{REPLY_PREFIX} {token}" in outcome.stdout, outcome.describe()


def test_gateway_binds_only_loopback(feature_host: FeatureHost, gateway: GatewayController) -> None:
    del gateway
    listeners = feature_host.run(["ss", "-H", "-ltn", "sport = :18789"], timeout=30).require_ok()
    addresses = [line.split()[3] for line in listeners.stdout.splitlines() if line.strip()]
    assert addresses, listeners.describe()
    for address in addresses:
        host = address.rsplit(":", 1)[0].strip("[]")
        assert host in {"127.0.0.1", "::1"}, f"gateway listens on non-loopback {address}"


def test_openclaw_doctor_is_clean(feature_host: FeatureHost) -> None:
    feature_host.openclaw("doctor", "--non-interactive", timeout=600).require_ok()


def test_security_audit_passes(feature_host: FeatureHost, gateway: GatewayController) -> None:
    del gateway
    feature_host.openclaw("security", "audit", "--deep", timeout=600).require_ok()


def test_secrets_audit_passes(feature_host: FeatureHost) -> None:
    feature_host.openclaw("secrets", "audit", "--check", timeout=600).require_ok()


def test_profile_allowlist_keeps_bundled_provider_plugins(feature_host: FeatureHost) -> None:
    allow = cast(dict[str, object], feature_host.rendered_config()["plugins"]).get("allow")
    statuses = plugin_statuses(feature_host)
    # The documented local model path (OLLAMA_API_KEY) needs OpenClaw's bundled ollama provider.
    assert (
        statuses.get("ollama") == "loaded"
    ), f"bundled provider plugin 'ollama' is {statuses.get('ollama')!r} under plugins.allow={allow}"
