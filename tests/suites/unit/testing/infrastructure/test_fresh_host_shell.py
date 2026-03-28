"""Unit coverage for fresh-host compose probe environment wiring."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any, cast

from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import shell as fresh_host_shell

_compose_probe_env = cast(
    Callable[..., dict[str, str]],
    cast(Any, fresh_host_shell)._compose_probe_env,
)


def test_compose_probe_env_inherits_repo_local_varlock_assignments(tmp_path: pathlib.Path) -> None:
    """Fresh-host compose probes should reuse repo-local Varlock secrets."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    local_env_file = repo_root / "platform" / "configs" / "varlock" / ".env.local"
    local_env_file.parent.mkdir(parents=True, exist_ok=True)
    local_env_file.write_text(
        "NEO4J_PASSWORD=probe-secret\nNEO4J_USERNAME=neo4j\n", encoding="utf-8"
    )
    env_overrides = {
        "HOME": str(home_dir),
        "STRONGCLAW_CONFIG_DIR": str(tmp_path / "managed-config"),
    }

    env = _compose_probe_env(
        env_overrides,
        repo_root_path=repo_root,
        compose_name="docker-compose.aux-stack.yaml",
        repo_local_state=True,
    )

    assert env["NEO4J_PASSWORD"] == "probe-secret"
    assert env["NEO4J_USERNAME"] == "neo4j"


def test_compose_probe_env_helper_uses_context_repo_local_state(tmp_path: pathlib.Path) -> None:
    """Context-aware compose probes should preserve the repo-local compose project wiring."""
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    context = fresh_host.prepare_context(
        scenario_id="macos-sidecars",
        repo_root=workspace,
        runner_temp=runner_temp,
        workspace=workspace,
        github_env_file=github_env,
    )
    compose_file = (
        workspace / "platform" / "compose" / "docker-compose.aux-stack.ci-hosted-macos.yaml"
    )
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}\n", encoding="utf-8")

    env = fresh_host_shell.compose_probe_env(
        context,
        compose_file=compose_file,
        repo_local_state=True,
    )

    assert env["STRONGCLAW_COMPOSE_STATE_DIR"].endswith("/.openclaw/repo-local-compose")
    assert "COMPOSE_PROJECT_NAME" in env
