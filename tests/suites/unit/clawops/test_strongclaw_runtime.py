from __future__ import annotations

import pathlib

from clawops.strongclaw_runtime import write_env_assignments


def test_write_env_assignments_uses_owner_only_permissions(tmp_path: pathlib.Path) -> None:
    """Env files written by StrongClaw should stay private to the current user."""

    env_file = tmp_path / ".env.local"

    write_env_assignments(env_file, {"OPENCLAW_GATEWAY_TOKEN": "token-value"})

    assert env_file.stat().st_mode & 0o777 == 0o600
