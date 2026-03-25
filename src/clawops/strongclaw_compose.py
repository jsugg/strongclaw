"""Compose file and project helpers for StrongClaw operational commands."""

from __future__ import annotations

import hashlib
import os
import pathlib
from typing import Final

from clawops.strongclaw_runtime import CommandError

COMPOSE_VARIANT_ENV_VAR: Final[str] = "STRONGCLAW_COMPOSE_VARIANT"
SUPPORTED_COMPOSE_VARIANTS: Final[frozenset[str]] = frozenset({"ci-hosted-macos"})


def active_compose_variant() -> str | None:
    """Return the requested compose variant when one is configured."""
    raw_value = os.environ.get(COMPOSE_VARIANT_ENV_VAR, "").strip()
    if not raw_value:
        return None
    if raw_value not in SUPPORTED_COMPOSE_VARIANTS:
        supported = ", ".join(sorted(SUPPORTED_COMPOSE_VARIANTS))
        raise CommandError(
            f"unsupported compose variant {raw_value!r}; expected one of: {supported}"
        )
    return raw_value


def resolve_compose_file(repo_root: pathlib.Path, compose_name: str) -> pathlib.Path:
    """Return the effective compose file for the active environment."""
    compose_dir = repo_root / "platform" / "compose"
    base_path = compose_dir / compose_name
    variant = active_compose_variant()
    if variant is None:
        return base_path
    variant_path = base_path.with_name(f"{base_path.stem}.{variant}{base_path.suffix}")
    if not variant_path.is_file():
        raise CommandError(f"compose variant file is missing: {variant_path.as_posix()}")
    return variant_path


def compose_project_name(
    *,
    compose_name: str,
    state_dir: pathlib.Path,
    repo_local_state: bool,
) -> str | None:
    """Return a deterministic project name when a compose variant is active."""
    if active_compose_variant() is None:
        return None
    compose_scope = "browser" if "browser-lab" in compose_name else "sidecars"
    state_scope = "repo" if repo_local_state else "host"
    digest = hashlib.sha256(state_dir.as_posix().encode("utf-8")).hexdigest()[:10]
    return f"strongclaw-{compose_scope}-{state_scope}-{digest}"
