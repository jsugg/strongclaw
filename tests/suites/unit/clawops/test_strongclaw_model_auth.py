"""Tests for StrongClaw model-auth readiness helpers."""

from __future__ import annotations

import pathlib

import pytest

from clawops import strongclaw_model_auth


def test_ensure_model_auth_skip_mode_bypasses_agent_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Skip mode should not require OpenClaw agent discovery during setup."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("OPENCLAW_MODEL_SETUP_MODE", "skip")
    monkeypatch.setattr(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        lambda repo_root: config_path,
    )
    monkeypatch.setattr(
        strongclaw_model_auth,
        "_all_agents_have_models",
        lambda *args, **kwargs: (_ for _ in ()).throw(
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Check-only mode should preserve real readiness checks even when skip is set."""
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[bool, int]] = []

    monkeypatch.setenv("OPENCLAW_MODEL_SETUP_MODE", "skip")
    monkeypatch.setattr(
        strongclaw_model_auth,
        "resolve_openclaw_config_path",
        lambda repo_root: config_path,
    )
    monkeypatch.setattr(
        strongclaw_model_auth,
        "_all_agents_have_models",
        lambda repo_root, *, probe, probe_max_tokens: calls.append((probe, probe_max_tokens))
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
