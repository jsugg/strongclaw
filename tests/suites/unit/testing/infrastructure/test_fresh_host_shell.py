"""Unit coverage for fresh-host compose probe environment wiring."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any, cast

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

    env = _compose_probe_env(
        {"HOME": str(home_dir)},
        repo_root_path=repo_root,
        compose_name="docker-compose.aux-stack.yaml",
        repo_local_state=True,
    )

    assert env["NEO4J_PASSWORD"] == "probe-secret"
    assert env["NEO4J_USERNAME"] == "neo4j"
