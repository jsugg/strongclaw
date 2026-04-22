"""Unit tests for strongclaw_compose.py."""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from clawops.strongclaw_compose import (
    COMPOSE_VARIANT_ENV_VAR,
    active_compose_variant,
    compose_project_name,
    resolve_compose_file,
)
from clawops.strongclaw_runtime import CommandError

# ---------------------------------------------------------------------------
# active_compose_variant
# ---------------------------------------------------------------------------


def test_active_compose_variant_returns_none_when_unset() -> None:
    assert active_compose_variant(environ={}) is None


def test_active_compose_variant_returns_none_for_empty_string() -> None:
    assert active_compose_variant(environ={COMPOSE_VARIANT_ENV_VAR: ""}) is None


def test_active_compose_variant_returns_none_for_whitespace() -> None:
    assert active_compose_variant(environ={COMPOSE_VARIANT_ENV_VAR: "   "}) is None


def test_active_compose_variant_returns_value_for_valid_variant() -> None:
    result = active_compose_variant(environ={COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"})
    assert result == "ci-hosted-macos"


def test_active_compose_variant_raises_for_unknown_variant() -> None:
    with pytest.raises(CommandError, match="unsupported compose variant"):
        active_compose_variant(environ={COMPOSE_VARIANT_ENV_VAR: "nonexistent-variant"})


# ---------------------------------------------------------------------------
# compose_project_name
# ---------------------------------------------------------------------------


def test_compose_project_name_returns_none_when_no_variant() -> None:
    result = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=pathlib.Path("/tmp/state"),
        repo_local_state=False,
        environ={},
    )
    assert result is None


def test_compose_project_name_returns_string_when_variant_active() -> None:
    state_dir = pathlib.Path("/tmp/state")
    result = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ={COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"},
    )
    assert result is not None
    assert result.startswith("strongclaw-sidecars-")


def test_compose_project_name_includes_host_scope_for_non_repo_local() -> None:
    state_dir = pathlib.Path("/tmp/state")
    result = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ={COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"},
    )
    assert result is not None
    assert "-host-" in result


def test_compose_project_name_includes_repo_scope_for_repo_local() -> None:
    state_dir = pathlib.Path("/tmp/state")
    result = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=True,
        environ={COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"},
    )
    assert result is not None
    assert "-repo-" in result


def test_compose_project_name_uses_browser_scope_for_browser_lab() -> None:
    state_dir = pathlib.Path("/tmp/state")
    result = compose_project_name(
        compose_name="docker-compose.browser-lab.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ={COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"},
    )
    assert result is not None
    assert "browser" in result


def test_compose_project_name_is_deterministic() -> None:
    state_dir = pathlib.Path("/tmp/deterministic-state")
    env = {COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"}
    result_a = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ=env,
    )
    result_b = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ=env,
    )
    assert result_a == result_b


def test_compose_project_name_digest_derived_from_state_dir() -> None:
    state_dir = pathlib.Path("/tmp/specific-state")
    env = {COMPOSE_VARIANT_ENV_VAR: "ci-hosted-macos"}
    result = compose_project_name(
        compose_name="docker-compose.aux-stack.yaml",
        state_dir=state_dir,
        repo_local_state=False,
        environ=env,
    )
    expected_digest = hashlib.sha256(state_dir.as_posix().encode("utf-8")).hexdigest()[:10]
    assert result is not None
    assert result.endswith(expected_digest)


# ---------------------------------------------------------------------------
# resolve_compose_file
# ---------------------------------------------------------------------------


def test_resolve_compose_file_returns_base_path_when_no_variant(tmp_path: pathlib.Path) -> None:
    # Use a real repo root so resolve_asset_path can find the compose dir
    repo_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
    result = resolve_compose_file(repo_root, "docker-compose.aux-stack.yaml")
    assert result.name == "docker-compose.aux-stack.yaml"


def test_resolve_compose_file_variant_path_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPOSE_VARIANT_ENV_VAR, "ci-hosted-macos")
    # Use a real repo root but a compose name that has no ci-hosted-macos variant file
    repo_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
    with pytest.raises(CommandError, match="compose variant file is missing"):
        resolve_compose_file(repo_root, "docker-compose.langfuse.optional.yaml")
