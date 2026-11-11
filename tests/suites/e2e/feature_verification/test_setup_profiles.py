"""Setup-profile features: each profile renders, validates, and boots a real gateway.

Each test activates its own profile through the operator path (``clawops config memory
--set-profile`` for managed profiles, ``clawops setup --profile`` for legacy ones), re-applies the
model chain as the setup guidance instructs, and restarts the harness gateway on it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Final, cast

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


def test_openclaw_qmd_profile_installs_and_uses_qmd(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "openclaw-qmd")
    qmd = feature_host.home / ".bun" / "bin" / "qmd"
    feature_host.run([str(qmd), "status"], timeout=120).require_ok()
    memory = cast(dict[str, object], feature_host.rendered_config()["memory"])
    assert memory["backend"] == "qmd", memory
    status = feature_host.openclaw("memory", "status", "--deep", timeout=600).require_ok()
    assert "qmd" in status.output.lower(), status.describe()


def test_acp_profile_registers_acp_worker_agents(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "acp")
    listed = _agent_ids(feature_host)
    for agent_id in ACP_WORKER_AGENTS:
        assert agent_id in listed, listed
    agents = cast(dict[str, object], feature_host.rendered_config()["agents"])
    runtimes = {
        str(cast(dict[str, object], agent)["id"]): cast(dict[str, object], agent).get("runtime")
        for agent in cast(list[object], agents["list"])
    }
    for agent_id in ACP_WORKER_AGENTS:
        runtime = cast(dict[str, object], runtimes[agent_id])
        assert runtime["type"] == "acp", runtime
        assert cast(dict[str, object], runtime["acp"])["backend"] == "acpx", runtime


def test_acp_worker_agent_runs_a_turn_through_acpx(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    _activate(feature_host, gateway, "acp")
    agents = cast(dict[str, object], feature_host.rendered_config()["agents"])
    coder = next(
        cast(dict[str, object], agent)
        for agent in cast(list[object], agents["list"])
        if cast(dict[str, object], agent)["id"] == "coder-acp-codex"
    )
    acp_cwd = Path(
        str(cast(dict[str, object], cast(dict[str, object], coder["runtime"])["acp"])["cwd"])
    )
    acp_cwd.mkdir(parents=True, exist_ok=True)
    stub = feature_host.repo_root / STUB_AGENT
    (acp_cwd / ".acpxrc.json").write_text(
        json.dumps(
            {"agents": {"codex": {"command": f"{feature_host.python} {stub} --persona codex"}}}
        ),
        encoding="utf-8",
    )
    token = f"FV-worker-{uuid.uuid4().hex[:10]}"
    outcome = feature_host.openclaw(
        "agent",
        "--agent",
        "coder-acp-codex",
        "--session-id",
        f"featver-{token}",
        "--message",
        f"Reply {token}",
        "--json",
        timeout=300,
    )
    replies = reply_texts(outcome.require_ok().json())
    assert any(ACP_STUB_REPLY_PREFIX in reply and token in reply for reply in replies), (
        f"the ACP worker did not answer through acpx (bundled acpx plugin: "
        f"{plugin_statuses(feature_host).get('acpx')!r}); agent replies: {replies}"
    )


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
