"""Wire StrongClaw's model chain to the fake model server, the way an operator would.

The provider is declared as an explicit OpenAI-compatible entry under ``models.providers``
(core OpenClaw config, independent of bundled provider plugins), and the operator env contract
selects it through StrongClaw's env-driven chain (``OPENCLAW_DEFAULT_MODEL``). ``clawops setup``
re-renders ``openclaw.json``, so the provider is re-registered after every render.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from clawops.strongclaw_runtime import (
    resolve_openclaw_config_path,
    set_env_assignment,
    varlock_local_env_file,
)
from tests.utils.helpers._fresh_host.shell import phase_env
from tests.utils.helpers._fresh_host.storage import load_context

PROVIDER_ID: Final[str] = "featver"
CHAT_MODEL: Final[str] = "featver-chat"
FAKE_CHAT_MODEL_REF: Final[str] = f"{PROVIDER_ID}/{CHAT_MODEL}"
PROVIDER_BASE_URL: Final[str] = "http://127.0.0.1:18000/v1"
# Not a credential: the fake server accepts any key; OpenClaw only needs one to be present.
PROVIDER_API_KEY: Final[str] = "featver-local"
ENV_CONTRACT: Final[dict[str, str]] = {
    "OPENCLAW_DEFAULT_MODEL": FAKE_CHAT_MODEL_REF,
    "HYPERMEMORY_EMBEDDING_MODEL": "ollama/nomic-embed-text",
    "HYPERMEMORY_EMBEDDING_API_BASE": "http://host.docker.internal:11434",
}
MODEL_AUTH_TIMEOUT_SECONDS: Final[int] = 900
SYSTEMD_GATEWAY_UNIT: Final[str] = "openclaw-gateway.service"
LAUNCHD_GATEWAY_LABEL: Final[str] = "ai.openclaw.gateway"


class ProviderError(RuntimeError):
    """Raised when the fake provider cannot be wired into the host."""


def provider_entry() -> dict[str, object]:
    """Return the ``models.providers`` entry for the fake model server."""
    return {
        "baseUrl": PROVIDER_BASE_URL,
        "apiKey": PROVIDER_API_KEY,
        "api": "openai-completions",
        "models": [
            {
                "id": CHAT_MODEL,
                "name": "Feature verification fake model",
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 131072,
                "maxTokens": 8192,
            }
        ],
    }


def seed_env_contract(context_file: Path) -> Path:
    """Select the fake provider in the operator env contract (before bootstrap)."""
    context = load_context(context_file)
    env_file = varlock_local_env_file(
        Path(context.repo_root), home_dir=Path(context.app_home), environ=phase_env(context)
    )
    env_file.parent.mkdir(parents=True, exist_ok=True)
    for key, value in ENV_CONTRACT.items():
        set_env_assignment(env_file, key, value)
    env_file.chmod(0o600)
    return env_file


def register_provider(config_path: Path) -> None:
    """Add the fake provider to a rendered ``openclaw.json``."""
    if not config_path.is_file():
        raise ProviderError(f"rendered OpenClaw config not found at {config_path}")
    payload = cast(dict[str, object], json.loads(config_path.read_text(encoding="utf-8")))
    models = payload.setdefault("models", {})
    if not isinstance(models, dict):
        raise ProviderError(f"{config_path}: `models` is not a mapping")
    providers = cast(dict[str, object], models).setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ProviderError(f"{config_path}: `models.providers` is not a mapping")
    cast(dict[str, object], providers)[PROVIDER_ID] = provider_entry()
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _restart_supervised_gateway(platform: str, env: Mapping[str, str]) -> str:
    if platform == "linux":
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", SYSTEMD_GATEWAY_UNIT],
            env=dict(env),
            check=False,
        )
        if active.returncode != 0:
            return "no supervised gateway"
        command = ["systemctl", "--user", "restart", SYSTEMD_GATEWAY_UNIT]
    else:
        command = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_GATEWAY_LABEL}"]
    restarted = subprocess.run(command, env=dict(env), capture_output=True, text=True, check=False)
    return "restarted" if restarted.returncode == 0 else f"restart failed: {restarted.stderr}"


def apply_model_provider(context_file: Path) -> dict[str, object]:
    """Register the provider, run ``clawops model-auth ensure``, and restart the gateway."""
    context = load_context(context_file)
    env = {**phase_env(context), "OPENCLAW_MODEL_SETUP_MODE": "auto"}
    repo_root = Path(context.repo_root)
    config_path = resolve_openclaw_config_path(
        repo_root, home_dir=Path(context.app_home), environ=env
    )
    register_provider(config_path)
    ensure = subprocess.run(
        [str(repo_root / ".venv" / "bin" / "python"), "-m", "clawops", "model-auth", "ensure"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=MODEL_AUTH_TIMEOUT_SECONDS,
        check=False,
    )
    return {
        "config": str(config_path),
        "modelAuthExitCode": ensure.returncode,
        "modelAuthOutput": ensure.stdout[-2000:],
        "gateway": _restart_supervised_gateway(context.platform, env),
    }
