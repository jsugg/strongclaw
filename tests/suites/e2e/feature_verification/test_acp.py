"""ACP worker features: acp-runner through the real acpx in every backend auth mode.

The ``openclaw`` backend runs fully live (acpx -> ``openclaw acp`` -> gateway -> provider).
Vendor backends replace only the vendor adapter with an ACP stub agent through acpx's own
agent-override config, so StrongClaw's credential broker, env sanitization and the real acpx
client are exercised without vendor accounts.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from tests.utils.helpers.feature_verification import (
    ACP_STUB_REPLY_PREFIX,
    REPLY_PREFIX,
    FeatureHost,
    GatewayController,
)

STUB_AGENT: Final[Path] = Path("tests/utils/helpers/_feature_verification/acp_stub_agent.py")
STUBBED_AGENTS: Final[tuple[str, ...]] = ("codex", "claude", "gemini", "qwen", "copilot", "cursor")
FAKE_KEY: Final[str] = "featver-not-a-real-key"


@dataclass(frozen=True, slots=True)
class AcpSession:
    """One finished acp-runner session."""

    summary: dict[str, object]
    stdout: str

    def stub_reply(self) -> str:
        """Return the stub agent's reply line from the session transcript."""
        for line in self.stdout.splitlines():
            if ACP_STUB_REPLY_PREFIX in line:
                return line
        raise AssertionError(f"no stub reply in acpx output: {self.stdout[-2000:]}")


def _workspace(feature_host: FeatureHost, *, stubbed: bool) -> tuple[Path, Path]:
    root = feature_host.scratch("acp")
    project = root / "project"
    workspace = project / "workspace"
    workspace.mkdir(parents=True)
    if stubbed:
        stub = feature_host.repo_root / STUB_AGENT
        agents = {
            name: {"command": f"{feature_host.python} {stub} --persona {name}"}
            for name in STUBBED_AGENTS
        }
        (workspace / ".acpxrc.json").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return project, workspace


def _vendor_cli_stubs(feature_host: FeatureHost) -> Path:
    """Stand-in vendor CLIs that report a logged-in subscription, as the real ones do."""
    bin_dir = feature_host.scratch("vendor-bin")
    scripts = {
        "codex": "#!/bin/sh\necho 'Logged in using ChatGPT'\n",
        "claude": '#!/bin/sh\necho \'{"loggedIn": true, "authMethod": "claude.ai"}\'\n',
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _run_session(
    feature_host: FeatureHost,
    *,
    backend: str,
    auth_mode: str,
    prompt: str,
    stubbed: bool = True,
    env: dict[str, str] | None = None,
) -> AcpSession:
    project, workspace = _workspace(feature_host, stubbed=stubbed)
    outcome = feature_host.clawops(
        "acp-runner",
        "--backend",
        backend,
        "--auth-mode",
        auth_mode,
        "--prompt",
        prompt,
        "--project-root",
        str(project),
        "--workspace",
        str(workspace),
        "--allowed-workspace-root",
        str(project),
        "--state-dir",
        str(project / "state"),
        "--permissions-mode",
        "approve-reads",
        "--output-format",
        "json",
        "--timeout-seconds",
        "240",
        timeout=300,
        env=env,
    )
    summary = cast(dict[str, object], outcome.json())
    stdout_path = summary.get("stdout_path")
    stdout = Path(str(stdout_path)).read_text(encoding="utf-8") if stdout_path else ""
    assert summary.get("status") == "succeeded", f"{outcome.describe()}\n{stdout[-2000:]}"
    return AcpSession(summary=summary, stdout=stdout)


def _env_with(feature_host: FeatureHost, **values: str) -> dict[str, str]:
    env = {"PATH": f"{_vendor_cli_stubs(feature_host)}:{feature_host.product_env()['PATH']}"}
    env.update(values)
    return env


def test_openclaw_backend_runs_a_live_acp_turn(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    del gateway
    token = f"FV-acp-{uuid.uuid4().hex[:10]}"
    session = _run_session(
        feature_host, backend="openclaw", auth_mode="local", prompt=f"Reply {token}", stubbed=False
    )
    assert (
        f"{REPLY_PREFIX} {token}" in session.stdout
    ), f"openclaw acp did not return the provider reply:\n{session.stdout[-3000:]}"


def test_codex_api_mode_passes_the_key_and_authenticates(feature_host: FeatureHost) -> None:
    session = _run_session(
        feature_host,
        backend="codex",
        auth_mode="api",
        prompt="FV-codex-api",
        env=_env_with(feature_host, OPENAI_API_KEY=FAKE_KEY),
    )
    reply = session.stub_reply()
    assert "OPENAI_API_KEY:present" in reply, reply
    assert "auth=openai-api-key" in reply, f"acpx did not select the env-backed auth: {reply}"
    assert session.summary["credential_source_class"] == "process-env", session.summary


def test_codex_subscription_mode_strips_api_keys(feature_host: FeatureHost) -> None:
    session = _run_session(
        feature_host,
        backend="codex",
        auth_mode="subscription",
        prompt="FV-codex-sub",
        env=_env_with(feature_host, OPENAI_API_KEY=FAKE_KEY),
    )
    reply = session.stub_reply()
    assert "OPENAI_API_KEY:absent" in reply and "auth=none" in reply, reply
    assert session.summary["credential_source_class"] == "vendor-cli-state", session.summary


def test_claude_api_mode_passes_the_key(feature_host: FeatureHost) -> None:
    session = _run_session(
        feature_host,
        backend="claude",
        auth_mode="api",
        prompt="FV-claude-api",
        env=_env_with(feature_host, ANTHROPIC_API_KEY=FAKE_KEY),
    )
    assert "ANTHROPIC_API_KEY:present" in session.stub_reply()


def test_claude_subscription_mode_strips_api_keys(feature_host: FeatureHost) -> None:
    session = _run_session(
        feature_host,
        backend="claude",
        auth_mode="subscription",
        prompt="FV-claude-sub",
        env=_env_with(feature_host, ANTHROPIC_API_KEY=FAKE_KEY),
    )
    assert "ANTHROPIC_API_KEY:absent" in session.stub_reply()
    assert session.summary["credential_source_class"] == "vendor-cli-state", session.summary


def test_claude_cloud_provider_mode_keeps_cloud_credentials(feature_host: FeatureHost) -> None:
    session = _run_session(
        feature_host,
        backend="claude",
        auth_mode="cloud-provider",
        prompt="FV-claude-cloud",
        env=_env_with(feature_host, ANTHROPIC_API_KEY=FAKE_KEY, AWS_PROFILE="featver"),
    )
    reply = session.stub_reply()
    assert "ANTHROPIC_API_KEY:absent" in reply and "AWS_PROFILE:present" in reply, reply
    assert session.summary["credential_source_class"] == "cloud-env", session.summary


def test_local_backends_dispatch_through_acpx(feature_host: FeatureHost) -> None:
    for backend in ("gemini", "qwen", "copilot", "cursor"):
        session = _run_session(
            feature_host, backend=backend, auth_mode="local", prompt=f"FV-{backend}"
        )
        reply = session.stub_reply()
        assert f"persona={backend}" in reply and f"prompt=FV-{backend}" in reply, reply


def test_auth_policy_blocks_unsupported_and_unready_modes(feature_host: FeatureHost) -> None:
    project, workspace = _workspace(feature_host, stubbed=True)
    common = (
        "--prompt",
        "FV-policy",
        "--project-root",
        str(project),
        "--workspace",
        str(workspace),
        "--allowed-workspace-root",
        str(project),
        "--state-dir",
        str(project / "state"),
    )
    unsupported = feature_host.clawops(
        "acp-runner", "--backend", "codex", "--auth-mode", "cloud-provider", *common
    )
    payload = cast(dict[str, object], unsupported.json())
    assert not unsupported.ok and payload["status"] == "preflight_failed", unsupported.describe()
    assert payload["credential_state"] == "forbidden_by_policy", payload
    env = _env_with(feature_host)
    env["PATH"] = f"{feature_host.scratch('empty-bin')}:{feature_host.product_env()['PATH']}"
    unready = feature_host.clawops(
        "acp-runner",
        "--backend",
        "codex",
        "--auth-mode",
        "api",
        *common,
        env={**env, "OPENAI_API_KEY": ""},
    )
    unready_payload = cast(dict[str, object], unready.json())
    assert unready_payload["status"] == "preflight_failed", unready.describe()


def test_credential_status_reports_each_mode(feature_host: FeatureHost) -> None:
    ready = feature_host.clawops(
        "credential-status",
        "--backend",
        "claude",
        "--auth-mode",
        "cloud-provider",
        env={"AWS_PROFILE": "featver"},
    )
    ready_payload = cast(dict[str, object], ready.json())
    assert ready_payload["state"] == "ready" and ready_payload["source_class"] == "cloud-env"
    missing = feature_host.clawops(
        "credential-status", "--backend", "codex", "--auth-mode", "api", env={"OPENAI_API_KEY": ""}
    )
    assert cast(dict[str, object], missing.json())["state"] == "misconfigured", missing.describe()
