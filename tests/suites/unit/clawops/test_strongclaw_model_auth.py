"""Tests for StrongClaw model-auth readiness helpers."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest

from clawops import strongclaw_model_auth
from tests.plugins.infrastructure.context import TestContext

pytestmark = pytest.mark.test_profile("model_setup_skip")


def test_ensure_model_auth_skip_mode_bypasses_agent_probe(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Skip mode should not require OpenClaw agent discovery during setup."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    def _resolve_openclaw_config_path(repo_root: pathlib.Path) -> pathlib.Path:
        del repo_root
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

    def _resolve_openclaw_config_path(repo_root: pathlib.Path) -> pathlib.Path:
        del repo_root
        return config_path

    def _all_agents_have_models(
        repo_root: pathlib.Path,
        *,
        probe: bool,
        probe_max_tokens: int,
    ) -> tuple[bool, list[str]]:
        del repo_root
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
        Callable[[pathlib.Path], list[str]],
        vars(strongclaw_model_auth)["_list_agent_ids"],
    )
    assert list_agent_ids(tmp_path) == ["admin", "reader"]


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
        Callable[[pathlib.Path, str], bool],
        vars(strongclaw_model_auth)["_agent_models_available_via_list"],
    )
    assert models_available(tmp_path, "admin") is True


def test_ensure_model_auth_non_interactive_mode_skips_wizard_fallback(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Non-interactive setup should fail closed instead of launching the wizard."""
    test_context.env.set("OPENCLAW_MODEL_SETUP_MODE", "auto")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    def _resolve_openclaw_config_path(repo_root: pathlib.Path) -> pathlib.Path:
        del repo_root
        return config_path

    def _all_agents_have_models(
        repo_root: pathlib.Path,
        *,
        probe: bool,
        probe_max_tokens: int,
    ) -> tuple[bool, list[str]]:
        del repo_root, probe, probe_max_tokens
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
        Callable[[pathlib.Path], str],
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
        "guidance": guidance_text(tmp_path),
    }
