"""Tests for StrongClaw model-auth readiness helpers."""

from __future__ import annotations

import pathlib

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
