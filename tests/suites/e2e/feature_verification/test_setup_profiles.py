"""Setup-profile features: each profile renders, validates, and boots a real gateway.

Each test activates its own profile through the operator path (``clawops config memory
--set-profile`` for managed profiles, ``clawops setup --profile`` for legacy ones), re-applies the
model chain as the setup guidance instructs, and restarts the harness gateway on it.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Final, cast

from clawops.openclaw_agents import agent_entries, default_agent_id
from clawops.strongclaw_runtime import resolve_openclaw_config_path
from tests.utils.helpers.feature_verification import (
    ACP_STUB_REPLY_PREFIX,
    FeatureHost,
    GatewayController,
    plugin_statuses,
    register_provider,
    reply_texts,
)

ACP_WORKER_AGENTS: Final[tuple[str, ...]] = ("coder-acp-codex", "reviewer-acp-claude")
STUB_AGENT: Final[Path] = Path("tests/utils/helpers/_feature_verification/acp_stub_agent.py")
DISPATCHER_SESSION_KEY: Final[str] = "agent:acp-dispatch:main"
SPAWNED_SESSION_KEY: Final[re.Pattern[str]] = re.compile(r"agent:[a-z0-9-]+:acp:[0-9a-f-]{36}")
COMMAND_REPLY_TIMEOUT_SECONDS: Final[float] = 120.0
ACP_BACKEND_READY_TIMEOUT_SECONDS: Final[float] = 120.0


def _activate(feature_host: FeatureHost, gateway: GatewayController, profile: str) -> None:
    if profile in {"acp", "browser-lab"}:
        feature_host.clawops(
            "setup",
            "--asset-root",
            ".",
            "--home-dir",
            str(feature_host.home),
            "--profile",
            profile,
            "--skip-bootstrap",
            "--no-activate-services",
            "--no-verify",
            "--non-interactive",
            timeout=1800,
        ).require_ok()
    else:
        payload = (
            feature_host.clawops(
                "config",
                "--asset-root",
                ".",
                "--home-dir",
                str(feature_host.home),
                "memory",
                "--set-profile",
                profile,
                "--json",
                timeout=1800,
            )
            .require_ok()
            .json()
        )
        assert cast(dict[str, object], payload).get("ok", True) is not False, payload
    # Setup re-renders openclaw.json, so the operator re-declares the provider first.
    register_provider(resolve_openclaw_config_path(feature_host.repo_root))
    feature_host.clawops("model-auth", "--asset-root", ".", "ensure", timeout=900).require_ok()
    feature_host.openclaw("config", "validate", timeout=120).require_ok()
    gateway.restart()


def _agent_ids(feature_host: FeatureHost) -> list[str]:
    payload = feature_host.openclaw("agents", "list", "--json", timeout=120).require_ok().json()
    return [str(cast(dict[str, object], agent)["id"]) for agent in cast(list[object], payload)]


def test_openclaw_default_profile_boots_with_memory_core(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "openclaw-default")
    plugins = cast(dict[str, object], feature_host.rendered_config()["plugins"])
    slots = cast(dict[str, object], plugins["slots"])
    assert slots == {"contextEngine": "legacy", "memory": "memory-core"}, slots
    assert plugin_statuses(feature_host).get("memory-core") == "loaded"
    feature_host.openclaw("memory", "status", "--deep", timeout=600).require_ok()


def test_openclaw_qmd_profile_indexes_the_qmd_corpus_with_builtin_memory(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    """OpenClaw 2026.8 removed the QMD backend (docs/concepts/memory-builtin.md, "Migrating from
    QMD"): the profile still installs QMD and serves its corpus through builtin `extraPaths`."""
    _activate(feature_host, gateway, "openclaw-qmd")
    qmd = feature_host.home / ".bun" / "bin" / "qmd"
    feature_host.run([str(qmd), "status"], timeout=120).require_ok()
    memory = cast(dict[str, object], feature_host.rendered_config()["memory"])
    assert {"backend", "qmd"}.isdisjoint(memory), memory
    runbooks = (feature_host.repo_root / "platform" / "docs").resolve().as_posix()
    extra_paths = cast(
        list[dict[str, str]], cast(dict[str, object], memory["search"])["extraPaths"]
    )
    assert runbooks in [Path(entry["path"]).resolve().as_posix() for entry in extra_paths]
    feature_host.openclaw("memory", "status", "--deep", timeout=600).require_ok()
    owner = default_agent_id(feature_host.rendered_config())
    feature_host.openclaw("memory", "index", "--agent", owner, "--force", timeout=900).require_ok()
    payload = (
        feature_host.openclaw(
            "memory",
            "search",
            "--agent",
            owner,
            "--query",
            "StrongClaw degradation contract dependency health runtime impact",
            "--max-results",
            "5",
            "--json",
            timeout=600,
        )
        .require_ok()
        .json()
    )
    result = cast(dict[str, object], payload)
    assert result.get("stale") is not True, result
    hits = [
        str(cast(dict[str, object], hit)["path"]) for hit in cast(list[object], result["results"])
    ]
    assert any("/platform/docs/" in path for path in hits), hits


def test_acp_profile_registers_acp_worker_agents(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "acp")
    listed = _agent_ids(feature_host)
    for agent_id in ACP_WORKER_AGENTS:
        assert agent_id in listed, listed
    runtimes = {
        agent_id: agent.get("runtime")
        for agent_id, agent in agent_entries(feature_host.rendered_config()).items()
    }
    for agent_id in ACP_WORKER_AGENTS:
        runtime = cast(dict[str, object], runtimes[agent_id])
        assert runtime["type"] == "acp", runtime
        assert cast(dict[str, object], runtime["acp"])["backend"] == "acpx", runtime


def _set_stub_harness(feature_host: FeatureHost, harness: str) -> None:
    """Point the acpx plugin's *harness* command at the ACP stub agent (operator config)."""
    config_path = resolve_openclaw_config_path(feature_host.repo_root)
    payload = cast(dict[str, object], json.loads(config_path.read_text(encoding="utf-8")))
    entries = cast(dict[str, object], cast(dict[str, object], payload["plugins"])["entries"])
    acpx = cast(dict[str, object], entries.setdefault("acpx", {}))
    settings = cast(dict[str, object], acpx.setdefault("config", {}))
    agents = cast(dict[str, object], settings.setdefault("agents", {}))
    agents[harness] = {
        "command": str(feature_host.python),
        "args": [str(feature_host.repo_root / STUB_AGENT), "--persona", harness],
    }
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _session_reply(
    feature_host: FeatureHost,
    session_key: str,
    message: str,
    *,
    timeout: float = COMMAND_REPLY_TIMEOUT_SECONDS,
) -> str:
    """Send *message* to *session_key* through the gateway as operator.admin; return the reply."""
    sent_at_ms = time.time() * 1000
    feature_host.gateway_admin_call(
        "chat.send",
        {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": f"featver-{uuid.uuid4().hex}",
        },
    ).require_ok()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history = feature_host.gateway_admin_call(
            "chat.history", {"sessionKey": session_key, "limit": 1}
        ).require_ok()
        messages = cast(list[object], cast(dict[str, object], history.json()).get("messages", []))
        last = cast(dict[str, object], messages[-1]) if messages else {}
        if (
            last.get("role") == "assistant"
            and float(cast(float, last.get("timestamp", 0))) >= sent_at_ms - 1000
        ):
            return " ".join(
                str(cast(dict[str, object], part).get("text", ""))
                for part in cast(list[object], last.get("content", []))
            )
        time.sleep(1)
    recent = feature_host.gateway_admin_call(
        "chat.history", {"sessionKey": session_key, "limit": 6}
    ).json()
    entries = cast(list[object], cast(dict[str, object], recent).get("messages", []))
    summary = [
        f"{cast(dict[str, object], entry).get('role')}: "
        f"{json.dumps(cast(dict[str, object], entry).get('content'))[:300]}"
        for entry in entries
    ]
    raise AssertionError(f"no reply to {message!r} from session {session_key}; recent: {summary}")


def _dispatcher_command(feature_host: FeatureHost, command: str) -> str:
    """Send a gateway command from the dispatcher session as operator.admin; return the reply."""
    return _session_reply(feature_host, DISPATCHER_SESSION_KEY, command)


def test_acp_worker_agent_runs_a_turn_through_acpx(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "acp")
    coder = agent_entries(feature_host.rendered_config())["coder-acp-codex"]
    acp = cast(dict[str, object], cast(dict[str, object], coder["runtime"])["acp"])
    acp_cwd = Path(str(acp["cwd"]))
    acp_cwd.mkdir(parents=True, exist_ok=True)
    harness = str(acp["agent"])
    _set_stub_harness(feature_host, harness)
    gateway.restart()
    # Setup installs acpx through `openclaw plugins install`, the only way it is trusted.
    acpx = cast(
        dict[str, object],
        feature_host.openclaw("plugins", "inspect", "acpx", "--json", timeout=120)
        .require_ok()
        .json(),
    )
    assert cast(dict[str, object], acpx["plugin"]).get("trustedOfficialInstall") is True, acpx

    # The acpx backend registers asynchronously after the gateway reports ready.
    deadline = time.monotonic() + ACP_BACKEND_READY_TIMEOUT_SECONDS
    doctor = _dispatcher_command(feature_host, "/acp doctor")
    while "healthy: yes" not in doctor and time.monotonic() < deadline:
        time.sleep(2)
        doctor = _dispatcher_command(feature_host, "/acp doctor")
    assert "healthy: yes" in doctor, doctor

    spawned = _dispatcher_command(
        feature_host,
        f"/acp spawn {harness} --mode persistent --bind off --thread off --cwd {acp_cwd}",
    )
    session_key = SPAWNED_SESSION_KEY.search(spawned)
    assert session_key is not None, f"/acp spawn did not start a session: {spawned}"

    token = f"FV-worker-{uuid.uuid4().hex[:10]}"
    # OpenClaw 2026.8's `openclaw agent` CLI requires the key's agent segment to be a configured
    # agent (docs/cli/agent.md), but an ACP key carries the harness id (`agent:codex:acp:<uuid>`,
    # docs/tools/acp-agents.md). The Gateway `agent` method that CLI calls still routes the key, and
    # gateway `chat.send` only records the message for an unbound ACP session.
    params = {
        "sessionKey": session_key.group(0),
        "message": f"Reply {token}",
        "idempotencyKey": f"featver-{uuid.uuid4().hex}",
    }
    outcome = feature_host.openclaw(
        "gateway",
        "call",
        "agent",
        "--params",
        json.dumps(params),
        "--expect-final",
        "--json",
        timeout=300,
    )
    replies = reply_texts(outcome.require_ok().json())
    assert any(
        ACP_STUB_REPLY_PREFIX in reply and token in reply for reply in replies
    ), f"the ACP worker did not answer through acpx; replies: {replies}"

    # An ordinary turn addressed to the unsandboxed dispatcher never reaches the model.
    probe = f"FV-dispatch-{uuid.uuid4().hex[:10]}"
    started = time.time()
    refused = feature_host.openclaw(
        "agent", "--agent", "acp-dispatch", "--message", f"Reply {probe}", "--json", timeout=300
    )
    assert not refused.ok and "No callable tools remain" in refused.output, refused.describe()
    assert not any(
        probe in str(request.get("latestUser", ""))
        for request in feature_host.fake_model_requests()
        if float(cast(float, request.get("at", 0.0))) >= started
    ), "a dispatcher turn reached the model"


def test_browser_lab_profile_points_at_the_cdp_endpoint(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "browser-lab")
    browser = cast(dict[str, object], feature_host.rendered_config()["browser"])
    profiles = cast(dict[str, object], browser["profiles"])
    lab = cast(dict[str, object], profiles[str(browser["defaultProfile"])])
    assert browser["enabled"] is True and lab["cdpUrl"] == "http://127.0.0.1:9222", browser
    ssrf = cast(dict[str, object], browser["ssrfPolicy"])
    assert ssrf["dangerouslyAllowPrivateNetwork"] is False, ssrf
