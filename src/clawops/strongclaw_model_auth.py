"""Ensure the rendered OpenClaw config has a usable model chain."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
from collections.abc import Sequence
from typing import cast

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.strongclaw_runtime import (
    CommandError,
    load_env_assignments,
    load_openclaw_config,
    resolve_openclaw_config_path,
    run_command,
    run_command_inherited,
    run_openclaw_command,
    run_varlock_command,
    use_varlock_env_mode,
    value_is_effective,
    varlock_available,
    varlock_env_dir,
    varlock_local_env_file,
    wrap_command_with_varlock,
)

DEFAULT_PROBE_MAX_TOKENS = 16
JSON_DOCUMENT_START_RE = re.compile(r"(?m)^[ \t]*[\[{]")
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MIN_CONTEXT_WINDOW = 16_000
OLLAMA_CONTEXT_LENGTH_RE = re.compile(r"context length\s+(\d+)", re.IGNORECASE)


def _mapping_or_none(value: object) -> dict[str, object] | None:
    """Return a string-keyed mapping copy when *value* is mapping-like."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _mutable_mapping_or_none(value: object) -> dict[str, object] | None:
    """Return a mutable string-keyed mapping reference when *value* is mapping-like."""
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _extract_json_document(output: str) -> object | None:
    """Return the first JSON document found in mixed command output."""
    decoder = json.JSONDecoder()
    for match in JSON_DOCUMENT_START_RE.finditer(output):
        candidate = output[match.start() :].lstrip()
        try:
            payload, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        return cast(object, payload)
    return None


def _effective_env_assignments(repo_root: pathlib.Path) -> dict[str, str]:
    """Load the effective env contract, preferring a Varlock snapshot when available."""
    local_values = load_env_assignments(varlock_local_env_file(repo_root))
    if not varlock_available() or not varlock_env_dir(repo_root).is_dir():
        return local_values
    snapshot = run_varlock_command(
        repo_root,
        ["env"],
        timeout_seconds=15,
    )
    if not snapshot.ok:
        return local_values
    merged = dict(local_values)
    for raw_line in snapshot.stdout.splitlines():
        if not raw_line or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        if value_is_effective(merged.get(key)):
            continue
        merged[key] = value
    return merged


def _extract_agent_ids(payload: object) -> list[str]:
    """Extract OpenClaw agent ids from a JSON payload."""
    if isinstance(payload, list):
        agent_ids: list[str] = []
        for item in cast(Sequence[object], payload):
            item_mapping = _mapping_or_none(item)
            if item_mapping is not None and "id" in item_mapping:
                agent_ids.append(str(item_mapping["id"]))
        return agent_ids
    payload_mapping = _mapping_or_none(payload)
    if payload_mapping is not None:
        agents = payload_mapping.get("agents")
        if isinstance(agents, list):
            nested_agent_ids: list[str] = []
            for item in cast(Sequence[object], agents):
                item_mapping = _mapping_or_none(item)
                if item_mapping is not None and "id" in item_mapping:
                    nested_agent_ids.append(str(item_mapping["id"]))
            return nested_agent_ids
    return []


def _list_agent_ids(repo_root: pathlib.Path) -> list[str]:
    """Return the configured OpenClaw agent ids."""
    result = run_openclaw_command(repo_root, ["agents", "list", "--json"], timeout_seconds=30)
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "openclaw agents list failed"
        raise CommandError(detail, result=result)
    payload = _extract_json_document(result.stdout)
    if payload is None:
        raise CommandError("invalid JSON from `openclaw agents list --json`")
    agent_ids = _extract_agent_ids(payload)
    if not agent_ids:
        raise CommandError("OpenClaw does not have any configured agents to validate.")
    return agent_ids


def _models_status_supported(repo_root: pathlib.Path) -> bool:
    """Return whether `openclaw models status` is supported."""
    result = run_openclaw_command(
        repo_root,
        ["models", "status", "--help"],
        timeout_seconds=10,
    )
    return result.ok


def _agent_models_available_via_list(repo_root: pathlib.Path, agent_id: str) -> bool:
    """Return whether one agent has an available model according to list JSON."""
    result = run_openclaw_command(
        repo_root,
        ["models", "--agent", agent_id, "list", "--json"],
        timeout_seconds=30,
    )
    if not result.ok:
        return False
    payload = _extract_json_document(result.stdout)
    if payload is None:
        return False
    payload_mapping = _mapping_or_none(payload)
    if payload_mapping is None:
        return False
    models = payload_mapping.get("models")
    if not isinstance(models, list):
        return False
    return any(
        (model_mapping := _mapping_or_none(model)) is not None
        and model_mapping.get("available") is True
        for model in cast(Sequence[object], models)
    )


def _agent_model_status_ok(
    repo_root: pathlib.Path,
    agent_id: str,
    *,
    probe: bool,
    probe_max_tokens: int,
) -> bool:
    """Return whether one agent passes the status check."""
    command = ["models", "status", "--agent", agent_id, "--check"]
    if probe:
        command.extend(["--probe", "--probe-max-tokens", str(probe_max_tokens)])
    result = run_openclaw_command(repo_root, command, timeout_seconds=60)
    return result.ok


def _all_agents_have_models(
    repo_root: pathlib.Path,
    *,
    probe: bool,
    probe_max_tokens: int,
) -> tuple[bool, list[str]]:
    """Return whether every configured agent has a usable model."""
    agent_ids = _list_agent_ids(repo_root)
    failures: list[str] = []
    use_status = _models_status_supported(repo_root)
    for agent_id in agent_ids:
        ok = (
            _agent_model_status_ok(
                repo_root,
                agent_id,
                probe=probe,
                probe_max_tokens=probe_max_tokens,
            )
            if use_status
            else _agent_models_available_via_list(repo_root, agent_id)
        )
        if not ok:
            failures.append(agent_id)
    return not failures, failures


def _append_unique_model(target: list[str], candidate: str) -> None:
    """Append one model ref if it is non-empty and not already present."""
    normalized = candidate.strip()
    if normalized and normalized not in target:
        target.append(normalized)


def _build_model_chain(env_values: dict[str, str]) -> list[str]:
    """Build the preferred model chain from env-backed defaults."""
    model_chain: list[str] = []
    default_model = env_values.get("OPENCLAW_DEFAULT_MODEL", "").strip()
    fallback_csv = env_values.get("OPENCLAW_MODEL_FALLBACKS", "").strip()
    if default_model:
        _append_unique_model(model_chain, default_model)
        for candidate in fallback_csv.split(","):
            _append_unique_model(model_chain, candidate)
        return model_chain
    provider_defaults = (
        ("OPENAI_API_KEY", "openai/gpt-5.4"),
        ("ANTHROPIC_API_KEY", "anthropic/claude-opus-4-6"),
        ("ZAI_API_KEY", "zai/glm-5"),
    )
    for key, model_ref in provider_defaults:
        if value_is_effective(env_values.get(key)):
            _append_unique_model(model_chain, model_ref)
    if value_is_effective(env_values.get("OPENROUTER_API_KEY")):
        _append_unique_model(model_chain, "openrouter/auto")
    if value_is_effective(env_values.get("MOONSHOT_API_KEY")):
        _append_unique_model(model_chain, "moonshot/default")
    if value_is_effective(env_values.get("OLLAMA_API_KEY")):
        ollama_model = env_values.get("OPENCLAW_OLLAMA_MODEL", "").strip()
        if ollama_model:
            _append_unique_model(model_chain, f"ollama/{ollama_model}")
    return model_chain


def _apply_model_chain(repo_root: pathlib.Path, model_chain: Sequence[str]) -> None:
    """Apply one model chain to every configured agent."""
    if not model_chain:
        return
    primary, *fallbacks = [str(item) for item in model_chain]
    config_path = resolve_openclaw_config_path(repo_root)
    payload = load_openclaw_config(config_path)
    agents_payload = _mutable_mapping_or_none(payload.get("agents"))
    if agents_payload is None:
        raise CommandError(f"invalid OpenClaw config at {config_path}: missing `agents` mapping")
    defaults_payload = _mutable_mapping_or_none(agents_payload.get("defaults"))
    if defaults_payload is None:
        raise CommandError(
            f"invalid OpenClaw config at {config_path}: missing `agents.defaults` mapping"
        )
    model_registry = _mutable_mapping_or_none(defaults_payload.get("models"))
    if model_registry is None:
        model_registry = {}
        defaults_payload["models"] = model_registry
    for model_ref in (primary, *fallbacks):
        model_registry.setdefault(model_ref, {})
    local_ollama_models = [
        model_ref.removeprefix("ollama/")
        for model_ref in (primary, *fallbacks)
        if model_ref.startswith("ollama/")
    ]
    if local_ollama_models:
        models_payload = _mutable_mapping_or_none(payload.get("models"))
        if models_payload is None:
            models_payload = {}
            payload["models"] = models_payload
        providers_payload = _mutable_mapping_or_none(models_payload.get("providers"))
        if providers_payload is None:
            providers_payload = {}
            models_payload["providers"] = providers_payload
        providers_payload["ollama"] = {
            "baseUrl": OLLAMA_BASE_URL,
            "api": "ollama",
            "models": [_ollama_provider_model(model_name) for model_name in local_ollama_models],
        }
    configured_model = {"primary": primary, "fallbacks": list(fallbacks)}
    defaults_payload["model"] = configured_model
    agent_list = agents_payload.get("list")
    if not isinstance(agent_list, list):
        raise CommandError(f"invalid OpenClaw config at {config_path}: missing `agents.list` array")
    for raw_agent in cast(list[object], agent_list):
        agent = _mutable_mapping_or_none(raw_agent)
        if agent is None:
            raise CommandError(
                f"invalid OpenClaw config at {config_path}: agent entries must be mappings"
            )
        if "id" not in agent:
            raise CommandError(
                f"invalid OpenClaw config at {config_path}: agent entry missing `id`"
            )
        agent["model"] = {"primary": primary, "fallbacks": list(fallbacks)}
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _ollama_provider_model(model_name: str) -> dict[str, object]:
    """Build one OpenClaw provider entry for a local Ollama model."""
    result = run_command(["ollama", "show", model_name], timeout_seconds=30)
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "ollama show failed"
        raise CommandError(f"failed to inspect local Ollama model {model_name}: {detail}")
    match = OLLAMA_CONTEXT_LENGTH_RE.search(result.stdout)
    if match is None:
        raise CommandError(f"failed to parse context length for local Ollama model {model_name}")
    context_window = int(match.group(1))
    if context_window < OLLAMA_MIN_CONTEXT_WINDOW:
        raise CommandError(
            f"local Ollama model {model_name} exposes only {context_window} context tokens; "
            f"OpenClaw requires at least {OLLAMA_MIN_CONTEXT_WINDOW}."
        )
    return {
        "id": model_name,
        "name": model_name,
        "reasoning": "r1" in model_name.lower(),
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": context_window,
        "maxTokens": context_window,
    }


def _interactive_prompt_allowed() -> bool:
    """Return whether the current terminal can host the OpenClaw model wizard."""
    return os.isatty(0) and os.isatty(1)


def _guidance_text(repo_root: pathlib.Path) -> str:
    """Return the operator remediation guidance."""
    return (
        "OpenClaw does not have a usable assistant model yet.\n\n"
        "Supported setup paths:\n"
        "- Guided wizard: rerun `clawops model-auth ensure` in a terminal.\n"
        "- Direct provider auth: `openclaw models auth login --provider <id>`.\n"
        f"- Env-driven: set provider auth in {varlock_local_env_file(repo_root)} and optionally:\n"
        "  - OPENCLAW_DEFAULT_MODEL=openai/gpt-5.4\n"
        "  - OPENCLAW_MODEL_FALLBACKS=anthropic/claude-opus-4-6,zai/glm-5\n"
        "  - OLLAMA_API_KEY=ollama-local with OPENCLAW_OLLAMA_MODEL=<pulled-model-with-at-least-16000-context>\n"
    )


def ensure_model_auth(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    probe: bool,
    probe_max_tokens: int = DEFAULT_PROBE_MAX_TOKENS,
    allow_prompt: bool = True,
) -> dict[str, object]:
    """Ensure or validate that OpenClaw model auth is usable."""
    config_path = resolve_openclaw_config_path(repo_root)
    if not config_path.exists():
        raise CommandError(f"Rendered OpenClaw config not found at {config_path}.")
    setup_mode = os.environ.get("OPENCLAW_MODEL_SETUP_MODE", "auto").strip() or "auto"
    # Fresh-host setup uses skip mode to exercise bootstrap/service flows without
    # depending on provider auth or platform-specific OpenClaw agent discovery.
    if not check_only and setup_mode == "skip":
        return {"ok": True, "checkedOnly": False, "configured": False, "skipped": True}
    ready, missing_agents = _all_agents_have_models(
        repo_root,
        probe=probe,
        probe_max_tokens=probe_max_tokens,
    )
    if ready:
        return {"ok": True, "checkedOnly": check_only, "configured": False, "missingAgents": []}
    if check_only:
        return {
            "ok": False,
            "checkedOnly": True,
            "configured": False,
            "missingAgents": missing_agents,
            "guidance": _guidance_text(repo_root),
        }
    model_chain = _build_model_chain(_effective_env_assignments(repo_root))
    if model_chain:
        _apply_model_chain(repo_root, model_chain)
        ready, missing_agents = _all_agents_have_models(
            repo_root,
            probe=probe,
            probe_max_tokens=probe_max_tokens,
        )
        if ready:
            return {
                "ok": True,
                "checkedOnly": False,
                "configured": True,
                "modelChain": model_chain,
                "missingAgents": [],
            }
    if allow_prompt and setup_mode in {"auto", "prompt"} and _interactive_prompt_allowed():
        returncode = run_command_inherited(
            wrap_command_with_varlock(repo_root, ["openclaw", "configure", "--section", "model"]),
            cwd=repo_root,
            timeout_seconds=1800,
        )
        if returncode != 0:
            raise CommandError("interactive model setup failed")
        ready, missing_agents = _all_agents_have_models(
            repo_root,
            probe=probe,
            probe_max_tokens=probe_max_tokens,
        )
        if ready:
            return {
                "ok": True,
                "checkedOnly": False,
                "configured": True,
                "modelChain": model_chain,
                "missingAgents": [],
            }
    return {
        "ok": False,
        "checkedOnly": False,
        "configured": False,
        "missingAgents": missing_agents,
        "guidance": _guidance_text(repo_root),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the model-auth CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    parser.add_argument(
        "--env-mode",
        choices=("managed", "legacy"),
        default="managed",
        help="Varlock env source used for readiness checks (default: managed).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure")
    ensure_parser.add_argument("--probe", action="store_true")
    ensure_parser.add_argument("--probe-max-tokens", type=int, default=DEFAULT_PROBE_MAX_TOKENS)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--probe", action="store_true")
    check_parser.add_argument("--probe-max-tokens", type=int, default=DEFAULT_PROBE_MAX_TOKENS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for model-auth readiness."""
    args = parse_args(argv)
    with use_varlock_env_mode(str(args.env_mode), default="managed"):
        payload = ensure_model_auth(
            resolve_asset_root_argument(args, command_name="clawops model-auth"),
            check_only=args.command == "check",
            probe=bool(args.probe),
            probe_max_tokens=int(args.probe_max_tokens),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if bool(payload.get("ok")) else 1
