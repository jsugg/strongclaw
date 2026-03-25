"""Tests for StrongClaw operational compose-state wiring."""

from __future__ import annotations

import pathlib
import re

import pytest

from clawops import strongclaw_ops


def test_compose_state_dir_defaults_to_openclaw_state_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Sidecar operations should follow the configured OpenClaw state root."""
    openclaw_state_dir = tmp_path / ".openclaw"

    monkeypatch.delenv("STRONGCLAW_COMPOSE_STATE_DIR", raising=False)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_state_dir",
        lambda _repo_root: openclaw_state_dir,
    )

    assert strongclaw_ops._compose_state_dir(tmp_path, repo_local_state=False) == (
        openclaw_state_dir / "compose"
    )


def test_compose_state_dir_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """An explicit compose-state override should take precedence."""
    override_dir = tmp_path / "override-compose-state"

    monkeypatch.setenv("STRONGCLAW_COMPOSE_STATE_DIR", str(override_dir))
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_state_dir",
        lambda _repo_root: tmp_path / ".openclaw",
    )

    assert strongclaw_ops._compose_state_dir(tmp_path, repo_local_state=False) == override_dir


def test_compose_state_dir_repo_local_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Repo-local compose-state commands should honor the dedicated override."""
    override_dir = tmp_path / "repo-local-override"

    monkeypatch.setenv("STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR", str(override_dir))

    assert strongclaw_ops._compose_state_dir(tmp_path, repo_local_state=True) == override_dir


def test_compose_env_exports_openclaw_and_compose_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Compose subprocesses should receive aligned state-root environment variables."""
    openclaw_state_dir = tmp_path / ".openclaw"
    config_path = tmp_path / ".openclaw" / "openclaw.json"

    monkeypatch.delenv("STRONGCLAW_COMPOSE_STATE_DIR", raising=False)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_state_dir",
        lambda _repo_root: openclaw_state_dir,
    )
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        lambda _repo_root: config_path,
    )

    env = strongclaw_ops._compose_env(
        tmp_path,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert env["OPENCLAW_STATE_DIR"] == str(openclaw_state_dir)
    assert env["STRONGCLAW_COMPOSE_STATE_DIR"] == str(openclaw_state_dir / "compose")
    assert env["OPENCLAW_CONFIG"] == str(config_path)


def test_compose_env_sets_project_name_for_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Compose variants should derive deterministic project names from state roots."""
    openclaw_state_dir = tmp_path / ".openclaw"
    config_path = openclaw_state_dir / "openclaw.json"
    repo_local_dir = tmp_path / "repo-local"

    monkeypatch.setenv("STRONGCLAW_COMPOSE_VARIANT", "ci-hosted-macos")
    monkeypatch.setenv("STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR", str(repo_local_dir))
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_state_dir",
        lambda _repo_root: openclaw_state_dir,
    )
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        lambda _repo_root: config_path,
    )

    host_env = strongclaw_ops._compose_env(
        tmp_path,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )
    repo_env = strongclaw_ops._compose_env(
        tmp_path,
        repo_local_state=True,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert re.fullmatch(r"strongclaw-sidecars-host-[0-9a-f]{10}", host_env["COMPOSE_PROJECT_NAME"])
    assert re.fullmatch(r"strongclaw-sidecars-repo-[0-9a-f]{10}", repo_env["COMPOSE_PROJECT_NAME"])
    assert host_env["COMPOSE_PROJECT_NAME"] != repo_env["COMPOSE_PROJECT_NAME"]


def test_compose_path_uses_variant_file_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Hosted macOS CI should resolve the dedicated compose variant file."""
    compose_dir = tmp_path / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    base_path = compose_dir / "docker-compose.aux-stack.yaml"
    variant_path = compose_dir / "docker-compose.aux-stack.ci-hosted-macos.yaml"
    base_path.write_text("services: {}\n", encoding="utf-8")
    variant_path.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("STRONGCLAW_COMPOSE_VARIANT", "ci-hosted-macos")

    assert strongclaw_ops._compose_path(tmp_path, "docker-compose.aux-stack.yaml") == variant_path
