"""Tests for StrongClaw model-auth readiness helpers."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from clawops import strongclaw_model_auth
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.assets import make_asset_root

pytestmark = pytest.mark.test_profile("model_setup_skip")


def test_ensure_model_auth_skip_mode_bypasses_agent_probe(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Skip mode should not require OpenClaw agent discovery during setup."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    def _resolve_openclaw_config_path(
        repo_root: pathlib.Path,
        *,
        env_mode: str = "managed",
    ) -> pathlib.Path:
        del repo_root, env_mode
        return config_path

    def _raise_unexpected_probe(*args: object, **kwargs: object) -> tuple[bool, list[str]]:
        del args, kwargs
        raise AssertionError("agent probe should be skipped")

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_all_agents_have_models",
        new=_raise_unexpected_probe,
    )

    payload = strongclaw_model_auth.ensure_model_auth(tmp_path, check_only=False, probe=False)

    assert payload == {
        "ok": True,
        "checkedOnly": False,
        "configured": False,
        "skipped": True,
    }


def test_ensure_model_auth_check_only_still_inspects_agents_when_skip_is_set(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Check-only mode should preserve real readiness checks even when skip is set."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[bool, int]] = []

    def _resolve_openclaw_config_path(
        repo_root: pathlib.Path,
        *,
        env_mode: str = "managed",
    ) -> pathlib.Path:
        del repo_root, env_mode
        return config_path

    def _all_agents_have_models(
        repo_root: pathlib.Path,
        *,
        probe: bool,
        probe_max_tokens: int,
        env_mode: str = "managed",
    ) -> tuple[bool, list[str]]:
        del repo_root, env_mode
        calls.append((probe, probe_max_tokens))
        return True, []

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_all_agents_have_models",
        new=_all_agents_have_models,
    )

    payload = strongclaw_model_auth.ensure_model_auth(tmp_path, check_only=True, probe=True)

    assert payload == {
        "ok": True,
        "checkedOnly": True,
        "configured": False,
        "missingAgents": [],
    }
    assert calls == [(True, strongclaw_model_auth.DEFAULT_PROBE_MAX_TOKENS)]


def test_list_agent_ids_ignores_trailing_non_json_output(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Agent discovery should tolerate trailing plugin/provider logs on stdout."""
    stdout = (
        '[{"id":"admin"},{"id":"reader"}]\n'
        "[plugins] strongclaw-hypermemory startup preflight failed: boom\n"
        "[bedrock-discovery] Failed to list models: invalid token\n"
    )

    def _run_openclaw_command(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(ok=True, stdout=stdout, stderr="")

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_openclaw_command",
        new=_run_openclaw_command,
    )

    list_agent_ids = cast(
        Callable[..., list[str]],
        vars(strongclaw_model_auth)["_list_agent_ids"],
    )
    assert list_agent_ids(tmp_path, env_mode="managed") == ["admin", "reader"]


def test_agent_models_available_via_list_ignores_trailing_non_json_output(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Model availability parsing should ignore logs appended after JSON output."""
    stdout = (
        '{"models":[{"id":"ollama/llama3:latest","available":true}]}\n'
        "[plugins] strongclaw-hypermemory startup preflight failed: boom\n"
    )

    def _run_openclaw_command(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(ok=True, stdout=stdout, stderr="")

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_openclaw_command",
        new=_run_openclaw_command,
    )

    models_available = cast(
        Callable[..., bool],
        vars(strongclaw_model_auth)["_agent_models_available_via_list"],
    )
    assert models_available(tmp_path, "admin", env_mode="managed") is True


def test_ensure_model_auth_non_interactive_mode_skips_wizard_fallback(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Non-interactive setup should fail closed instead of launching the wizard."""
    test_context.env.set("OPENCLAW_MODEL_SETUP_MODE", "auto")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    def _resolve_openclaw_config_path(
        repo_root: pathlib.Path,
        *,
        env_mode: str = "managed",
    ) -> pathlib.Path:
        del repo_root, env_mode
        return config_path

    def _all_agents_have_models(
        repo_root: pathlib.Path,
        *,
        probe: bool,
        probe_max_tokens: int,
        env_mode: str = "managed",
    ) -> tuple[bool, list[str]]:
        del repo_root, probe, probe_max_tokens, env_mode
        return False, ["admin"]

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_all_agents_have_models",
        new=_all_agents_have_models,
    )

    def _build_model_chain(env_values: dict[str, str]) -> list[str]:
        del env_values
        return []

    def _run_command_inherited(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise AssertionError("wizard should stay disabled")

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_build_model_chain",
        new=_build_model_chain,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_command_inherited",
        new=_run_command_inherited,
    )

    guidance_text = cast(
        Callable[..., str],
        vars(strongclaw_model_auth)["_guidance_text"],
    )

    payload = strongclaw_model_auth.ensure_model_auth(
        tmp_path,
        check_only=False,
        probe=False,
        allow_prompt=False,
    )

    assert payload == {
        "ok": False,
        "checkedOnly": False,
        "configured": False,
        "missingAgents": ["admin"],
        "guidance": guidance_text(tmp_path, env_mode="managed"),
    }


def test_main_applies_requested_varlock_env_mode(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    observed_mode: dict[str, str] = {}

    def _resolve_asset_root_argument(*_args: object, **_kwargs: object) -> pathlib.Path:
        return tmp_path

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
        probe_max_tokens: int,
        env_mode: str,
    ) -> dict[str, object]:
        assert repo_root == tmp_path
        assert check_only is True
        assert probe is False
        assert probe_max_tokens == strongclaw_model_auth.DEFAULT_PROBE_MAX_TOKENS
        assert env_mode == "legacy"
        observed_mode["value"] = env_mode
        return {"ok": True}

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_asset_root_argument",
        new=_resolve_asset_root_argument,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "ensure_model_auth",
        new=_ensure_model_auth,
    )

    exit_code = strongclaw_model_auth.main(["--env-mode", "legacy", "check"])

    assert exit_code == 0
    assert observed_mode["value"] == "legacy"


def test_apply_model_chain_updates_rendered_openclaw_config_directly(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Model-chain application should rewrite defaults and agent overrides in config."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": {
                            "primary": "openai/gpt-5.4",
                            "fallbacks": ["anthropic/claude-opus-4-6"],
                        },
                        "models": {
                            "openai/gpt-5.4": {"alias": "gpt"},
                        },
                    },
                    "list": [
                        {"id": "admin"},
                        {"id": "reader", "model": {"primary": "zai/glm-5", "fallbacks": []}},
                        {
                            "id": "coder",
                            "model": {
                                "primary": "openai-codex/gpt-5.4",
                                "fallbacks": ["github-copilot/gpt-4o"],
                            },
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    def _resolve_openclaw_config_path(repo_root: pathlib.Path) -> pathlib.Path:
        del repo_root
        return config_path

    def _raise_unexpected_command(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        raise AssertionError("OpenClaw CLI mutation path should stay unused")

    def _run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        assert command == ["ollama", "show", "deepseek-r1:latest"]
        return SimpleNamespace(
            ok=True,
            stdout=(
                "  Model\n" "    architecture        qwen2\n" "    context length      131072\n"
            ),
            stderr="",
        )

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_openclaw_command",
        new=_raise_unexpected_command,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_command",
        new=_run_command,
    )

    apply_model_chain = cast(
        Callable[[pathlib.Path, list[str]], None],
        vars(strongclaw_model_auth)["_apply_model_chain"],
    )
    apply_model_chain(
        tmp_path,
        ["ollama/deepseek-r1:latest", "anthropic/claude-opus-4-6"],
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    expected_model = {
        "primary": "ollama/deepseek-r1:latest",
        "fallbacks": ["anthropic/claude-opus-4-6"],
    }

    assert payload["agents"]["defaults"]["model"] == expected_model
    assert payload["agents"]["defaults"]["models"]["openai/gpt-5.4"] == {"alias": "gpt"}
    assert payload["agents"]["defaults"]["models"]["ollama/deepseek-r1:latest"] == {}
    assert payload["agents"]["defaults"]["models"]["anthropic/claude-opus-4-6"] == {}
    assert payload["models"]["providers"]["ollama"] == {
        "baseUrl": "http://127.0.0.1:11434",
        "api": "ollama",
        "models": [
            {
                "id": "deepseek-r1:latest",
                "name": "deepseek-r1:latest",
                "reasoning": True,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 131072,
                "maxTokens": 131072,
            }
        ],
    }
    assert [agent["model"] for agent in payload["agents"]["list"]] == [
        expected_model,
        expected_model,
        expected_model,
    ]


def test_apply_model_chain_rejects_local_ollama_models_below_openclaw_context_floor(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Local Ollama models should fail closed when they cannot satisfy OpenClaw probes."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"models": {}}, "list": [{"id": "admin"}]}}),
        encoding="utf-8",
    )

    def _resolve_openclaw_config_path(repo_root: pathlib.Path) -> pathlib.Path:
        del repo_root
        return config_path

    def _run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        assert command == ["ollama", "show", "llama3:latest"]
        return SimpleNamespace(
            ok=True,
            stdout="  Model\n    context length      8192\n",
            stderr="",
        )

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_command",
        new=_run_command,
    )

    apply_model_chain = cast(
        Callable[[pathlib.Path, list[str]], None],
        vars(strongclaw_model_auth)["_apply_model_chain"],
    )

    with pytest.raises(strongclaw_model_auth.CommandError, match="requires at least 16000"):
        apply_model_chain(tmp_path, ["ollama/llama3:latest"])


def test_effective_env_assignments_preserves_local_model_chain_when_varlock_env_is_redacted(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Redacted Varlock snapshots should not overwrite local model selection keys."""
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            (
                "OPENCLAW_DEFAULT_MODEL=ollama/deepseek-r1:latest",
                "OLLAMA_API_KEY=ollama-local",
                "OPENCLAW_OLLAMA_MODEL=deepseek-r1:latest",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()

    def _varlock_local_env_file(*_args: object, **_kwargs: object) -> pathlib.Path:
        return env_file

    def _varlock_available() -> bool:
        return True

    def _varlock_env_dir(*_args: object, **_kwargs: object) -> pathlib.Path:
        return env_dir

    def _run_varlock_command(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            ok=True,
            stdout=(
                "OPENCLAW_DEFAULT_MODEL=ol▒▒▒▒▒\n"
                "OLLAMA_API_KEY=ol▒▒▒▒▒\n"
                "OPENCLAW_OLLAMA_MODEL=de▒▒▒▒▒\n"
                "OPENAI_API_KEY=sk-redacted\n"
            ),
            stderr="",
        )

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "varlock_local_env_file",
        new=_varlock_local_env_file,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "varlock_available",
        new=_varlock_available,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "varlock_env_dir",
        new=_varlock_env_dir,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "run_varlock_command",
        new=_run_varlock_command,
    )

    effective_env_assignments = cast(
        Callable[[pathlib.Path], dict[str, str]],
        vars(strongclaw_model_auth)["_effective_env_assignments"],
    )
    values = effective_env_assignments(tmp_path)

    assert values["OPENCLAW_DEFAULT_MODEL"] == "ollama/deepseek-r1:latest"
    assert values["OLLAMA_API_KEY"] == "ollama-local"
    assert values["OPENCLAW_OLLAMA_MODEL"] == "deepseek-r1:latest"
    assert values["OPENAI_API_KEY"] == "sk-redacted"


def test_model_auth_main_honors_env_mode_wrapper(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    requested_modes: list[str] = []

    @contextmanager
    def _use_varlock_env_mode(env_mode: str) -> Iterator[None]:
        requested_modes.append(env_mode)
        yield

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
        probe_max_tokens: int,
    ) -> dict[str, object]:
        assert repo_root == asset_root
        assert check_only is True
        assert probe is False
        assert probe_max_tokens == strongclaw_model_auth.DEFAULT_PROBE_MAX_TOKENS
        return {"ok": True}

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "use_varlock_env_mode",
        new=_use_varlock_env_mode,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "ensure_model_auth",
        new=_ensure_model_auth,
    )

    exit_code = strongclaw_model_auth.main(
        ["--asset-root", str(asset_root), "--env-mode", "legacy", "check"]
    )

    assert exit_code == 0
    assert requested_modes == ["legacy"]
