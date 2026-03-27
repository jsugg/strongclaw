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

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=lambda repo_root: config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_all_agents_have_models",
        new=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("agent probe should be skipped")
        ),
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

    test_context.patch.patch_object(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        new=lambda repo_root: config_path,
    )
    test_context.patch.patch_object(
        strongclaw_model_auth,
        "_all_agents_have_models",
        new=lambda repo_root, *, probe, probe_max_tokens: calls.append((probe, probe_max_tokens))
        or (True, []),
    )

    payload = strongclaw_model_auth.ensure_model_auth(tmp_path, check_only=True, probe=True)

    assert payload == {
        "ok": True,
        "checkedOnly": True,
        "configured": False,
        "missingAgents": [],
    }
    assert calls == [(True, strongclaw_model_auth.DEFAULT_PROBE_MAX_TOKENS)]
