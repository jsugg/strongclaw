"""Ensure the rendered OpenClaw config has a usable model chain."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from collections.abc import Sequence
from typing import cast

from clawops.strongclaw_runtime import (
    CommandError,
    load_env_assignments,
    resolve_openclaw_config_path,
    resolve_repo_root,
    run_command_inherited,
    run_openclaw_command,
    run_varlock_command,
    value_is_effective,
    varlock_available,
    varlock_env_dir,
    varlock_local_env_file,
    wrap_command_with_varlock,
)

DEFAULT_PROBE_MAX_TOKENS = 16


def _mapping_or_none(value: object) -> dict[str, object] | None:
    """Return a string-keyed mapping copy when *value* is mapping-like."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


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
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CommandError(f"invalid JSON from `openclaw agents list --json`: {exc}") from exc
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
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
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
    for agent_id in _list_agent_ids(repo_root):
        set_result = run_openclaw_command(
            repo_root,
            ["models", "--agent", agent_id, "set", primary],
            timeout_seconds=30,
        )
        if not set_result.ok:
            detail = (
                set_result.stderr.strip()
                or set_result.stdout.strip()
                or "failed to set primary model"
            )
            raise CommandError(detail, result=set_result)
        clear_result = run_openclaw_command(
            repo_root,
            ["models", "--agent", agent_id, "fallbacks", "clear"],
            timeout_seconds=30,
        )
        if not clear_result.ok:
            detail = (
                clear_result.stderr.strip()
                or clear_result.stdout.strip()
                or "failed to clear fallbacks"
            )
            raise CommandError(detail, result=clear_result)
        for fallback in fallbacks:
            add_result = run_openclaw_command(
                repo_root,
                ["models", "--agent", agent_id, "fallbacks", "add", fallback],
                timeout_seconds=30,
            )
            if not add_result.ok:
                detail = (
                    add_result.stderr.strip()
                    or add_result.stdout.strip()
                    or "failed to add fallback model"
                )
                raise CommandError(detail, result=add_result)


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
        "  - OLLAMA_API_KEY=ollama-local with OPENCLAW_OLLAMA_MODEL=<pulled-model>\n"
    )


def ensure_model_auth(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    probe: bool,
    probe_max_tokens: int = DEFAULT_PROBE_MAX_TOKENS,
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
    if setup_mode in {"auto", "prompt"} and _interactive_prompt_allowed():
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
    parser.add_argument("--repo-root", type=pathlib.Path, default=None)
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
    payload = ensure_model_auth(
        resolve_repo_root(args.repo_root),
        check_only=args.command == "check",
        probe=bool(args.probe),
        probe_max_tokens=int(args.probe_max_tokens),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if bool(payload.get("ok")) else 1
